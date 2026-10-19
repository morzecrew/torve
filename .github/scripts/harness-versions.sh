#!/usr/bin/env bash
# The harness version each seat definition installs, as the `<NAME>_VERSION=`
# assignments `bake.hcl` labels its seat images with (S-0112/D-5). Read off
# the definition's own annotated ARG (S-0112/D-7), the one Renovate bumps, so
# a bump moves the label with it and the bake keeps no copy to go stale.
#
# Usage: env $(.github/scripts/harness-versions.sh) docker buildx bake ...
set -euo pipefail

for dockerfile in sandboxes/*/Dockerfile; do
	name="$(basename "$(dirname "$dockerfile")")"
	variable="$(printf '%s' "$name" | tr '[:lower:]-' '[:upper:]_')_VERSION"
	# A definition without an ARG named for itself carries no harness: the base
	# and the battery.
	sed -n "s/^ARG ${variable}=\(.*\)$/${variable}=\1/p" "$dockerfile"
done
