# Sandbox image for the dsh harness (S-0004): harness baked in, never
# injected. The base carries node 24, which satisfies dsh's requirement, plus
# git, python3, uv and the engine's own CLI (S-0063/D-7).
ARG BASE=sandbox-base
FROM ${BASE}
# The harness version rides a pinned-default ARG (S-0033/D-3): a bump is a
# one-line reviewed diff, never a rebuild side effect.
ARG DSH_VERSION=0.1.1-rc.2
RUN npm install -g @deepseek-ai/dsh@${DSH_VERSION} && dsh --version
# `dsh plugin add` shells out to pnpm and exits 127 without it, which is how
# every `routine` attempt died once the hook branch stopped dying first
# (S-0072/D-3). Pinned like the harness above it, for the same reason.
ARG PNPM_VERSION=12.4.1
RUN npm install -g pnpm@${PNPM_VERSION} && pnpm --version
# What this definition puts inside the image (S-0063/D-14): `toolkit/` is
# `/opt/torve`, named for where its contents land, and one COPY installs the
# lot. `/opt/torve/equip` turns the equipment manifest into whatever this
# harness needs and `/opt/torve/run` invokes it; the engine names only what the
# attempt is.
COPY toolkit/ /opt/torve/
RUN chmod -R a+rX /opt/torve \
 && chmod +x /opt/torve/run /opt/torve/equip /opt/torve/report-usage
# The scope-guard plugin imports `FsError` from `@deepseek-ai/dsh-fs`, a peer
# dsh itself carries. It is installed from `/opt/torve`, outside dsh's tree, so
# node's upward walk never reaches those nested modules and the plugin fails to
# load with ERR_MODULE_NOT_FOUND — taking the whole profile down with it. The
# link is what a peer dependency assumes: the host provides it.
RUN ln -s /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules \
          /opt/torve/scope-guard-policy/node_modules
# Boot the profile this image ships, with the patches the seat really applies,
# and fail the build if the plugin tree does not load. Every one of them is a
# patch, and a patch that drops a key an entry requires fails at boot — which
# without this is three attempts dead at `wall 0s` and a green repository.
RUN /opt/torve/smoke

# No model file is baked (S-0063/D-15). A dsh model is a provider, a catalog
# entry and a default — all of which are a patch — and the seven overlays this
# image used to carry made the fleet's roster a property of the harness. `equip`
# renders one overlay from the seat's `env` instead, so adding a model is an
# edit to reviewed configuration.
#
# Headless dsh prints no usage either; the session log carries it, multi-frame
# zstd-compressed, and `/opt/torve/report-usage` sums it into the one JSON line
# `parse_metadata` reads.
