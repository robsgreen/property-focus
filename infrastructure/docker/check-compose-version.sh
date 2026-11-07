#!/usr/bin/env bash
# Fails, with a message that names the required and the installed version, unless the
# `docker compose` plugin is at least the version this repository's Compose files and
# scripts need. `make install` runs it on developer machines and CI, and dogfood's deploy.sh
# on the host, so an old Compose stops with this message instead of a schema error.
#
# 2.27.2 is the first release with `docker compose config --environment`, which deploy.sh
# reads the environment file through (https://github.com/docker/compose/releases/tag/v2.27.2).
# That also covers 2.23.1, the first with the inline `configs.<name>.content` both Compose
# files use (https://docs.docker.com/reference/compose-file/configs/).
#
# Usage: check-compose-version.sh. Exits 0 when Compose is recent enough, 1 otherwise.

set -euo pipefail

required=2.27.2

# The first version number in `docker compose version`'s output, which reads
# "Docker Compose version v2.40.3", "Docker Compose version v2.40.3-desktop.1" or, from some
# builds, just "2.40.3"; empty when Compose is missing.
found=$(docker compose version 2>/dev/null | grep -Eo '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n 1 || true)

# Whether version $1 is at least version $2, comparing major, minor and patch numerically.
at_least() {
	local IFS=.
	# shellcheck disable=SC2206 # splitting on dots is the point
	local have=($1) want=($2) index
	for index in 0 1 2; do
		((10#${have[index]:-0} == 10#${want[index]:-0})) && continue
		((10#${have[index]:-0} > 10#${want[index]:-0}))
		return
	done
	return 0
}

if [[ -z $found ]] || ! at_least "$found" "$required"; then
	echo "Docker Compose >= $required is required, found ${found:-none} — update Docker Desktop / the compose plugin" >&2
	exit 1
fi
