#!/usr/bin/env sh
# The dashboard is buildless (0032 A-76): one dependency-free HTML file.
# "Building" is copying the source into the wheel's package data, which
# keeps D-32.4's shipping mechanism and deletes the toolchain.
set -eu
cd "$(dirname "$0")/.."
cp index.html ../src/torve/_web/index.html
echo "bundle: src/torve/_web/index.html ($(wc -c < index.html) bytes)"
