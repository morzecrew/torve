#!/usr/bin/env bash
# Build the dashboard bundle into src/torve/_web, the wheel-package-data path
# `torve serve` serves from a development checkout (D-32.4). Node is a
# build-time concern and stays one: nothing at runtime or install touches it.
#
# Whether the produced bundle is committed to the repository is the release
# pipeline's decision, not this script's — the script's job ends at the
# output directory.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
web_dir="${repo_root}/web"
out_dir="${repo_root}/src/torve/_web"

cd "${web_dir}"

npm ci --no-audit --no-fund
npm run build

echo
echo "bundle written to ${out_dir}"
echo "restart 'torve serve' to pick it up"
