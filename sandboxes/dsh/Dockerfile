# Sandbox image for the dsh harness (S-0004): harness baked in, never
# injected. The base carries node 24, which satisfies dsh's requirement, plus
# git, python3, uv and the engine's own CLI (S-0063/D-7).
ARG BASE=sandbox-base
FROM ${BASE}
# The harness version rides a pinned-default ARG (S-0033/D-3): a bump is a
# one-line reviewed diff, never a rebuild side effect.
ARG DSH_VERSION=0.1.1-rc.2
RUN npm install -g @deepseek-ai/dsh@${DSH_VERSION} && dsh --version
# What this definition puts inside the image (S-0063/D-14): `rootfs/` mirrors
# the container filesystem, so where a file lands is where it is written and
# one COPY installs the lot. `/opt/torve/equip` turns the equipment manifest
# into whatever this harness needs and `/opt/torve/run` invokes it; the engine
# names only what the attempt is.
COPY rootfs/ /
RUN chmod -R a+rX /opt/torve \
 && chmod +x /opt/torve/run /opt/torve/equip /opt/torve/report-usage

# No model file is baked (S-0063/D-15). A dsh model is a provider, a catalog
# entry and a default — all of which are a patch — and the seven overlays this
# image used to carry made the fleet's roster a property of the harness. `equip`
# renders one overlay from the seat's `env` instead, so adding a model is an
# edit to reviewed configuration.
#
# Headless dsh prints no usage either; the session log carries it, multi-frame
# zstd-compressed, and `/opt/torve/report-usage` sums it into the one JSON line
# `parse_metadata` reads.
