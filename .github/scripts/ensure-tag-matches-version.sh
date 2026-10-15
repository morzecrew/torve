#!/usr/bin/env bash
# The version is the tag's (hatch-vcs), so there is no declared number to
# compare against: refuse a tag that is not a release version, which would
# otherwise build and publish whatever hatch-vcs makes of it. Given a
# directory, also refuse a build there that does not carry the tag's version,
# as a tree the tag does not describe exactly builds a development version.
# Reads GITHUB_REF_NAME.
set -euo pipefail

VERSION="${GITHUB_REF_NAME#v}"

if ! [[ "$GITHUB_REF_NAME" =~ ^v[0-9]+\.[0-9]+\.[0-9]+((a|b|rc)[0-9]+)?$ ]]; then
	echo "ERROR: tag '${GITHUB_REF_NAME}' is not a release version (vX.Y.Z, or vX.Y.ZrcN)"
	exit 1
fi

if [ $# -gt 0 ]; then
	for built in "$1/torve-${VERSION}.tar.gz" "$1/torve-${VERSION}-py3-none-any.whl"; do
		if [ ! -f "$built" ]; then
			echo "ERROR: the build has no ${built##*/}; it built:"
			ls "$1"
			exit 1
		fi
	done
fi

echo "OK: tag ${GITHUB_REF_NAME} is the version ${VERSION}"
