#!/usr/bin/env bash
# Refuse a release whose tag is not the version pyproject.toml declares. The
# version is static, so a tag cut without the bump would build the previous
# number under the new tag; PyPI refuses that upload, but only after CI ran.
# Reads GITHUB_REF_NAME.
set -euo pipefail

DECLARED="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"

if [ "${GITHUB_REF_NAME#v}" = "$DECLARED" ]; then
	echo "OK: tag matches the declared version $DECLARED"
else
	echo "ERROR: tag '${GITHUB_REF_NAME}' does not match pyproject.toml's version '$DECLARED'"
	echo "Bump [project].version, merge it to main, and tag that commit."
	exit 1
fi
