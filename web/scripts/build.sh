#!/usr/bin/env sh
# Build the dashboard into the wheel's package data (RFC 0032 D-32.4).
# Vite's output is deterministic for a given lockfile, so the vendored
# bundle is byte-comparable against a fresh build (D-32.5).
set -eu
cd "$(dirname "$0")/.."
npm ci
npm run build
echo "bundle: src/torve/_web ($(du -sh ../src/torve/_web | cut -f1))"
