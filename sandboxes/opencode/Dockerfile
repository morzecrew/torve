# Sandbox image for the opencode harness (S-0004, thin per S-0017/D-8):
# harness baked in, never injected; everything else comes from the base.
ARG BASE=sandbox-base
FROM ${BASE}
# The harness version rides a pinned-default ARG (S-0033/D-3), as the other
# seats' do (S-0112/D-7): the install was unpinned and floated onto whatever
# `opencode-ai` published last, so an image carried a harness no one reviewed.
# renovate: datasource=npm depName=opencode-ai
ARG OPENCODE_VERSION=1.19.3
RUN npm install -g opencode-ai@${OPENCODE_VERSION} && opencode --version
