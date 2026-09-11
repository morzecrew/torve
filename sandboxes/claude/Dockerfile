# Sandbox image for harness-backed agents (S-0004): the harness is baked into
# the image, never injected from the host. git, python3, uv and the engine's
# own CLI come from the base (S-0063/D-7); this adds claude and nothing else.
ARG BASE=sandbox-base
FROM ${BASE}
# The harness version rides a pinned-default ARG (S-0033/D-3): a bump is a
# one-line reviewed diff, never a rebuild side effect.
ARG CLAUDE_VERSION=2.1.252
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_VERSION} && claude --version

# What this definition puts inside the image (S-0063/D-14): `rootfs/` mirrors
# the container filesystem, so where a file lands is where it is written and
# one COPY installs the lot. `/opt/torve/equip` turns the equipment manifest
# into whatever this harness needs and `/opt/torve/run` invokes it; the engine
# names only what the attempt is.
COPY rootfs/ /
RUN chmod -R a+rX /opt/torve && chmod +x /opt/torve/run /opt/torve/equip

# Harness plugins are identity (RFC 0017 §3, D-17.4): the clones are baked at
# pinned refs, because a fetch at dispatch would put the network inside every
# attempt. Which of them are installed and enabled is not baked — the runtime
# renders that from the profile's declaration when the sandbox exists (S-0061
# D-6), so the ref pinned here and the ref written into the bookkeeping cannot
# disagree, which is what they did while three JSON files were kept by hand
# beside these ARGs.
#
# The seed is a seed, not a home: HOME is /tmp at run time and must stay
# writable by the invoking uid, so the tier command copies this into place. It
# lives under the /opt/torve/ toolkit (RFC 0033 §5.3, D-33.4); the old
# /opt/claude-seed path stays as a symlink for one transition revision.
ARG CAVEMAN_REF=81536f57b330
ARG PONYTAIL_REF=v4.9.0
RUN set -eu; \
    seed=/opt/torve/seed/.claude/plugins; \
    mkdir -p "$seed/marketplaces" "$seed/cache"; \
    for spec in "caveman:JuliusBrussee/caveman:$CAVEMAN_REF" \
                "ponytail:DietrichGebert/ponytail:$PONYTAIL_REF"; do \
      name=${spec%%:*}; rest=${spec#*:}; repo=${rest%:*}; ref=${rest##*:}; \
      git clone --quiet "https://github.com/$repo" "$seed/marketplaces/$name"; \
      git -C "$seed/marketplaces/$name" checkout --quiet "$ref"; \
      mkdir -p "$seed/cache/$name/$name"; \
      cp -r "$seed/marketplaces/$name" "$seed/cache/$name/$name/$ref"; \
    done; \
    ln -s /opt/torve/seed /opt/claude-seed
# Readable by every uid, and the plugin directory writable by root alone —
# the runtime writes the rendered bookkeeping there as root before the agent
# runs, and nothing inside the attempt may edit what it was equipped with.
RUN chmod -R a+rX /opt/torve/seed
