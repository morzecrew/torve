# Sandbox image for the MiMo Code harness (S-0004, thin per S-0017/D-8):
# opencode lineage; harness baked in, never injected.
ARG BASE=sandbox-base
FROM ${BASE}
# The harness version rides a pinned-default ARG (S-0033/D-3): a bump is a
# one-line reviewed diff, never a rebuild side effect.
ARG MIMO_VERSION=0.1.13
RUN npm install -g @mimo-ai/cli@${MIMO_VERSION} && mimo --version
# What this definition puts inside the image (S-0063/D-14): `toolkit/` is
# `/opt/torve`, named for where its contents land, and one COPY installs the
# lot. `/opt/torve/equip` turns the equipment manifest into whatever this
# harness needs and `/opt/torve/run` invokes it; the engine names only what the
# attempt is.
COPY toolkit/ /opt/torve/
RUN chmod -R a+rX /opt/torve && chmod +x /opt/torve/run /opt/torve/equip
