#!/usr/bin/env bash
# The release's images (S-0112/D-1, S-0112/D-4): build every target from the
# tag, load each into the runner, prove each runs the release the tag names,
# and push only what passed.
#
# Usage: release-images.sh <build|check|push>
#
# The workflow runs these as three steps in that order, so the checks stand
# between the build and the first push. One script rather than three because
# the images it acts on are one list, derived here once.
#
# Reads VERSION (the tag's version), REGISTRY (where the images are published),
# for `build` REVISION (the commit being built) and, for `check` and `push`, GH_TOKEN so
# the registry can be asked what is already published.
set -euo pipefail

verb="${1:?usage: release-images.sh <build|check|push>}"

VERSION="${VERSION:?VERSION must be set to the version the tag names}"
REGISTRY="${REGISTRY:?REGISTRY must name where the images are published}"

# A tag may not hold `+` (S-0112/D-3), so the reference carries the version
# with `+` written `-` — the same substitution `bake.hcl` makes.
TAG="${VERSION//+/-}"

HARNESS_LABEL="io.morze.torve.harness.version"

# The bake's tags, derived the way `sandbox.image_tag` derives them: the base
# is `sandbox-base`, every other definition under `sandboxes/` is
# `<name>-sandbox`.
baked() {
	local name
	for dockerfile in sandboxes/*/Dockerfile; do
		name="$(basename "$(dirname "$dockerfile")")"
		if [ "$name" = base ]; then
			printf 'sandbox-base\n'
		else
			printf '%s-sandbox\n' "$name"
		fi
	done
}

# `<registry>/<name>:<tag>`, the reference the bake builds and a push names.
references() {
	local name
	while read -r name; do
		printf '%s/%s:%s\n' "$REGISTRY" "$name" "$TAG"
	done < <(baked)
}

# The harness version the bake stamped into a seat image (S-0112/D-5), empty
# for the base and the battery, which carry no harness.
harness_version() {
	local value
	value="$(docker inspect --format "{{ index .Config.Labels \"${HARNESS_LABEL}\" }}" "$1" 2>/dev/null || true)"
	[ "$value" = "<no value>" ] && value=""
	printf '%s' "$value"
}

# The harness an image contains, the inverse of its tag: `claude-sandbox` is
# `claude`, which is the verb its Dockerfile runs with `--version`.
harness_of() {
	local repository="${1%%:*}"
	printf '%s' "${repository##*/}" | sed 's/-sandbox$//'
}

# S-0112/D-1: a published tag is never pushed twice. Whether the registry
# already holds this tag: true when it does, false when it does not, and a
# registry that cannot answer stops the script, because reading that as "not
# published" is how `push` would move a tag a digest already has.
published() {
	local package="$1" tag="$2" owner="${REGISTRY##*/}" existing
	if ! existing="$(gh api --paginate "orgs/${owner}/packages/container/${package}/versions" \
		--jq '.[].metadata.container.tags[]' 2>&1)"; then
		# Only a package the organisation has never published is a first
		# release; any other failure leaves the question unanswered.
		if grep -q 'HTTP 404' <<<"$existing" && gh api "orgs/${owner}" >/dev/null 2>&1; then
			return 1
		fi
		echo "::error::cannot ask the registry what ${package} has published: ${existing}" >&2
		exit 1
	fi
	# A fixed string: a tag's `.` is not a wildcard, so `0-5-0` never reads as `0.5.0`.
	printf '%s\n' "$existing" | grep -qxF -- "$tag"
}

# Push one reference unless it is already published. A rerun after a partial
# push therefore publishes what is still missing and leaves the rest as it is,
# rather than stopping on the first tag it finds. A push the registry refuses
# is tried three times: GHCR has answered `unknown blob` once and then accepted
# the same push (the 0.6.0 release).
push_missing() {
	local reference="$1" repository="${1%%:*}" attempt
	if published "${repository##*/}" "${reference##*:}"; then
		echo "::notice::${reference} is already published; it is not pushed again" >&2
		return 0
	fi
	for attempt in 1 2 3; do
		docker push "$reference" && return 0
		[ "$attempt" = 3 ] || sleep $((attempt * ${PUSH_RETRY_SECONDS:-15}))
	done
	echo "::error::${reference} was refused three times" >&2
	return 1
}

case "$verb" in
build)
	# Every target, the base included, with the tag's version and the commit
	# the revision label carries. `--load` and no `--push`: what is checked
	# below is the image this runner would push, and nothing has left it yet.
	REVISION="${REVISION:?REVISION must be the commit being built}"
	# shellcheck disable=SC2046 # the assignments split on purpose, one per seat.
	VERSION="$VERSION" REVISION="$REVISION" REGISTRY="$REGISTRY" \
		env $(bash .github/scripts/harness-versions.sh) \
		docker buildx bake --file bake.hcl --load default
	;;
check)
	while read -r image; do
		printed="$(docker run --rm "$image" torve --version)"
		if [ "$printed" != "$VERSION" ]; then
			echo "::error::${image} runs torve '${printed}', not the release '${VERSION}'" >&2
			exit 1
		fi

		version="$(harness_version "$image")"
		[ -z "$version" ] && continue

		printed="$(docker run --rm "$image" "$(harness_of "$image")" --version)"
		if ! grep -qwF -- "$version" <<<"$printed"; then
			echo "::error::${image} prints '${printed}', not the harness version '${version}' its label carries" >&2
			exit 1
		fi
	done < <(references)

	# Asked before anything is pushed, so a registry that cannot answer stops
	# the job here; a tag already published is named, and `push` skips it.
	while read -r image; do
		repository="${image%%:*}"
		tags=("${image##*:}")
		version="$(harness_version "$image")"
		[ -n "$version" ] && tags+=("${version}-torve${TAG}")

		for tag in "${tags[@]}"; do
			if published "${repository##*/}" "$tag"; then
				echo "::notice::${repository##*/}:${tag} is already published; push skips it" >&2
			fi
		done
	done < <(references)
	;;
push)
	while read -r image; do
		push_missing "$image"

		# A seat is also tagged with the version its harness prints, for a
		# person who wants that in the reference (S-0112/D-1).
		version="$(harness_version "$image")"
		[ -z "$version" ] && continue
		seat="${image%:*}:${version}-torve${TAG}"
		docker tag "$image" "$seat"
		push_missing "$seat"
	done < <(references)
	;;
*)
	echo "usage: release-images.sh <build|check|push>" >&2
	exit 2
	;;
esac