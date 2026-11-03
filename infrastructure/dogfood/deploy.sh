#!/usr/bin/env bash
# Deploys one commit to the dogfood host, or rolls the application back to an earlier one.
# Runs on the host. Each commit has its own release directory,
# PF_DOGFOOD_RELEASES_DIR/<commit> (default /srv/property-focus/releases/<commit>): a copy
# of the commit with frontend/dist holding the web app built for dogfood, which
# .github/workflows/deploy-dogfood.yml prepares. The symlink `current` next to the releases
# directory names the release that passed its health checks last; operators' commands use it.
#
# Usage:
#   deploy.sh <commit>               deploy <commit> from its release directory
#   deploy.sh --rollback <commit>    run an earlier release's application again
#
# PF_DOGFOOD_ENV_FILE (default /etc/property-focus/dogfood.env) holds the deployment-injected
# settings and secrets (D49); docs/operations.md lists them and the file's format. Compose
# is the only parser of that file: this script reads the values it needs through
# `docker compose config --environment`. Nothing here prints a secret.
#
# A deploy builds the backend and Keycloak images once, tags them with the commit, records
# their digests in releases.tsv, and promotes exactly those images: the migration task, the
# API, the MCP adapter and the scheduler all run them. Caddy and the collector are recreated
# on every deploy and rollback, so they read their release's Caddyfile, collector
# configuration and web build. A rollback restores the API, MCP adapter and scheduler images,
# the web app, the Caddyfile and the collector configuration of the earlier release. It never
# reverses a migration (migrations follow expand/contract) and leaves Keycloak, its database
# and the realm as they are, because Keycloak cannot run an older version on a newer database.
#
# Cloudflare edge (A5): before a deploy changes anything, it checks that the release's
# cloudflare-ips.txt equals Cloudflare's published ranges, that the live firewall is exactly
# what firewall.sh applies (read through `sudo -n iptables -S`; docs/operations.md), and that
# the origin certificate chains to cloudflare-origin-ca.pem, covers both hostnames, matches
# its key, has more than 30 days left, and that the key has mode 600. The deploy workflow
# writes the certificate and key as cert.pem.new and key.pem.new beside the live pair in
# PF_DOGFOOD_ORIGIN_DIR (default /etc/property-focus/origin); the preflight checks that new
# pair, and only once the whole preflight passes is it moved over the live pair, so a rejected
# deploy leaves Caddy's next restart and any rollback on the pair that was live. Without a new
# pair the preflight checks the live one. Caddy trusts client-IP headers only from the ranges
# in cloudflare-ips.txt, which this script passes to Compose as PF_CLOUDFLARE_IP_RANGES.
#
# The unit tests run this script on developer machines too (macOS among them), so it keeps
# to commands and options that BSD userland and LibreSSL's openssl also have. Only
# switch_current's `mv -T`, which the tests never reach, assumes GNU coreutils on the host.

set -euo pipefail

usage() {
	echo "usage: $0 [--rollback] <commit>" >&2
	exit 2
}

mode=deploy
if [[ ${1:-} == --rollback ]]; then
	mode=rollback
	shift
fi
[[ $# -eq 1 && $1 =~ ^[0-9a-f]{7,40}$ ]] || usage
commit=$1
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

env_file=${PF_DOGFOOD_ENV_FILE:-/etc/property-focus/dogfood.env}
releases_dir=${PF_DOGFOOD_RELEASES_DIR:-/srv/property-focus/releases}
release=$releases_dir/$commit
dogfood_dir=$release/infrastructure/dogfood
# The Cloudflare Origin CA roots, and Caddy's live origin certificate and key, beside which
# the deploy workflow writes a new pair (<file>.new).
origin_ca=$dogfood_dir/cloudflare-origin-ca.pem
origin_dir=${PF_DOGFOOD_ORIGIN_DIR:-/etc/property-focus/origin}
origin_certificate=$origin_dir/cert.pem
origin_key=$origin_dir/key.pem
current=$(dirname "$releases_dir")/current
digests=${PF_DOGFOOD_RELEASES_FILE:-/var/lib/property-focus/releases.tsv}
# Images this repository builds: property-focus-<name>:<commit>.
built_images=(task keycloak api mcp scheduler)
# The services a rollback runs again, and the images they need.
application_services=(api mcp scheduler)

log() { printf '%s deploy: %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() {
	log "FAILED: $*"
	exit 1
}

# Checks that every line of the environment file is a comment, blank, or KEY=value with a
# bare value or a single-quoted one, so Compose reads each value exactly as written. Bare
# values cannot hold $, quotes, backslashes, # or white space; single-quoted ones cannot hold
# a single quote. Messages name the line and key, never the value.
check_env_file() {
	local line key value number=0
	local single_quoted="^'[^']*'\$" bare="^[^[:space:]\$'\"\\\\#]*\$"
	[[ -r $env_file ]] || fail "cannot read $env_file"
	while IFS= read -r line || [[ -n $line ]]; do
		number=$((number + 1))
		[[ -z ${line//[[:space:]]/} || $line =~ ^[[:space:]]*# ]] && continue
		[[ $line =~ ^([A-Z_][A-Z0-9_]*)=(.*)$ ]] || fail "$env_file line $number is not KEY=value"
		key=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
		if [[ ! $value =~ $single_quoted && ! $value =~ $bare ]]; then
			fail "$env_file line $number ($key): single-quote a value that holds \$, quotes, \\, # or spaces; a quoted value cannot hold a single quote"
		fi
	done <"$env_file"
}

compose() {
	docker compose --file "$dogfood_dir/compose.yaml" --env-file "$env_file" "$@"
}

# Exports the PF_* values of the environment file as Compose reads them.
load_environment() {
	local line environment
	environment=$(compose config --environment) || fail "Compose cannot read $env_file"
	while IFS= read -r line; do
		if [[ $line =~ ^(PF_[A-Z0-9_]*)=(.*)$ ]]; then
			export "${BASH_REMATCH[1]}=${BASH_REMATCH[2]}"
		fi
	done <<<"$environment"
}

image_digest() { docker image inspect --format '{{.Id}}' "property-focus-$1:$commit"; }

# Checks that each named image still carries the digest recorded for this commit.
verify_recorded_digests() {
	local name recorded
	for name in "$@"; do
		recorded=$(awk -F '\t' -v c="$commit" -v n="$name" '$2 == c && $3 == n { d = $4 } END { print d }' "$digests" 2>/dev/null || true)
		[[ -n $recorded ]] || fail "no recorded $name image for $commit in $digests"
		[[ $(image_digest "$name" 2>/dev/null) == "$recorded" ]] || fail "property-focus-$name:$commit is not the recorded image"
	done
}

# Retries a request until it answers with the expected status, for up to two minutes (while
# the services start). Usage: expect_status <status> <url> [curl options].
expect_status() {
	local expected=$1 url=$2 status deadline=$((SECONDS + 120))
	shift 2
	while :; do
		status=$(curl --silent --output /dev/null --write-out '%{http_code}' "$@" "$url" || true)
		[[ $status == "$expected" ]] && return 0
		((SECONDS < deadline)) || fail "$url answered $status, expected $expected"
		sleep 3
	done
}

# A public HTTPS request sent to this host's Caddy, which must present an origin certificate
# for the hostname from the Cloudflare Origin CA. Usage: expect_public <status> <host> <path>.
expect_public() { expect_status "$1" "https://$2$3" --resolve "$2:443:127.0.0.1" --cacert "$origin_ca"; }

# Fails unless the JSON document the app host serves at <path> names the realm on the public
# auth host, the PF_OIDC_ISSUER the services verify tokens against, in its <field>: a string,
# or a list whose first entry counts. Usage: expect_app_host_issuer <path> <field>.
expect_app_host_issuer() {
	local path=$1 field=$2
	local expected="https://$PF_DOGFOOD_AUTH_HOST/realms/property-focus" issuer
	expect_public 200 "$PF_DOGFOOD_APP_HOST" "$path"
	issuer=$(curl --silent --fail --resolve "$PF_DOGFOOD_APP_HOST:443:127.0.0.1" --cacert "$origin_ca" \
		"https://$PF_DOGFOOD_APP_HOST$path" |
		python3 -c '
import json, sys
value = json.load(sys.stdin).get(sys.argv[1], "")
print(value[0] if isinstance(value, list) and value else value)
' "$field") || true
	[[ $issuer == "$expected" ]] || fail "$PF_DOGFOOD_APP_HOST$path names issuer '$issuer', expected $expected"
}

health_checks() {
	log "health checks through Caddy and on loopback"
	expect_public 200 "$PF_DOGFOOD_APP_HOST" /health/ready
	# Protected-resource metadata, in the MCP SDK's path-inserted form and the root form.
	expect_app_host_issuer /.well-known/oauth-protected-resource/mcp authorization_servers
	expect_app_host_issuer /.well-known/oauth-protected-resource authorization_servers
	# Authorization-server metadata at the app host's root, for MCP clients of the 2025-03-26
	# protocol.
	expect_app_host_issuer /.well-known/oauth-authorization-server issuer
	expect_public 200 "$PF_DOGFOOD_APP_HOST" /
	expect_public 404 "$PF_DOGFOOD_APP_HOST" /.well-known/openid-configuration
	expect_public 200 "$PF_DOGFOOD_AUTH_HOST" /realms/property-focus/.well-known/openid-configuration
	expect_public 200 "$PF_DOGFOOD_AUTH_HOST" /.well-known/openid-configuration/realms/property-focus
	expect_public 404 "$PF_DOGFOOD_AUTH_HOST" /admin/
	expect_public 404 "$PF_DOGFOOD_AUTH_HOST" /realms/master/
	expect_status 200 http://127.0.0.1:5080/healthz
}

# Recreates Caddy and the collector, whose configuration files are single-file bind mounts
# that a running container never rereads. Caddy also rereads the origin certificate.
recreate_proxy_and_collector() {
	compose up --detach --wait --no-build --no-deps --force-recreate caddy otel-collector
}

# Points `current` at this release, atomically.
switch_current() {
	ln -sfn "$release" "$current.next"
	mv -fT "$current.next" "$current"
}

# Runs keycloak_realm.py over the loopback port with the realm placeholder values the
# keycloak container gets, read from the resolved Compose file. Compose writes a literal $
# as $$ there; the values are unescaped so the script sees what Keycloak sees.
realm() {
	(
		eval "$(compose config --format json | python3 -c '
import json, shlex, sys
environment = json.load(sys.stdin)["services"]["keycloak"]["environment"]
for name, value in environment.items():
    if name.startswith("PF_"):
        value = value.replace("$$", "$")
        print(f"export {name}={shlex.quote(value)}")
')"
		python3 "$release/backend/scripts/keycloak_realm.py" "$@" --url http://localhost:8080
	)
}

# The CIDRs of cloudflare-ips.txt, one per line, comments and blank lines dropped.
cloudflare_ranges() { awk '$1 !~ /^#/ && NF { print $1 }' "$dogfood_dir/cloudflare-ips.txt"; }

# Fails unless the committed ranges equal the lists Cloudflare publishes now.
check_published_ranges() {
	local published
	published=$(curl --silent --show-error --fail --max-time 30 https://api.cloudflare.com/client/v4/ips |
		python3 -c '
import json, sys
result = json.load(sys.stdin)["result"]
print("\n".join(result["ipv4_cidrs"] + result["ipv6_cidrs"]))
') || fail "cannot read Cloudflare's published ranges from https://api.cloudflare.com/client/v4/ips"
	diff <(sort <<<"$published") <(cloudflare_ranges | sort) >&2 ||
		fail "cloudflare-ips.txt differs from Cloudflare's published ranges (< published, > committed); refresh it as docs/operations.md says"
}

# Whether the deploy workflow left a new origin certificate or key to install.
has_new_origin_pair() { [[ -e $origin_certificate.new || -e $origin_key.new ]]; }

# Whether <certificate> names <host> in its subject alternative names, exactly or through a
# wildcard (*.example.com) for one leftmost label. Read from `openssl x509 -text`, because
# LibreSSL's openssl has no -checkhost. Usage: certificate_covers <certificate> <host>.
certificate_covers() {
	local host name
	host=$(tr '[:upper:]' '[:lower:]' <<<"$2")
	while IFS= read -r name; do
		[[ $name == "$host" ]] && return 0
		[[ $name == '*.'* && $host == *.* && ${host%%.*} != '' && ${host#*.} == "${name#*.}" ]] && return 0
	done < <(openssl x509 -in "$1" -noout -text | awk '
		names { gsub(/[[:space:]]/, ""); n = split(tolower($0), entries, ",")
			for (i = 1; i <= n; i++) if (entries[i] ~ /^dns:/) print substr(entries[i], 5)
			names = 0 }
		/X509v3 Subject Alternative Name:/ { names = 1 }')
	return 1
}

# Fails unless <certificate> and <key> are in place and usable for both hostnames.
# Usage: check_origin_certificate <certificate> <key>.
check_origin_certificate() {
	local certificate=$1 key=$2 host
	[[ -r $certificate && -r $key ]] || fail "no origin certificate and key at $certificate and $key"
	# find -perm with a plain mode matches exactly those permission bits (POSIX; no stat -c).
	[[ -n $(find "$key" -prune -perm 600) ]] || fail "$key must have mode 600"
	openssl verify -CAfile "$origin_ca" "$certificate" >/dev/null ||
		fail "$certificate is not issued by the Cloudflare Origin CA"
	openssl x509 -in "$certificate" -noout -checkend $((30 * 24 * 3600)) >/dev/null ||
		fail "$certificate expires within 30 days; renew it as docs/operations.md says"
	for host in "$PF_DOGFOOD_APP_HOST" "$PF_DOGFOOD_AUTH_HOST"; do
		certificate_covers "$certificate" "$host" ||
			fail "$certificate does not cover $host"
	done
	[[ $(openssl x509 -in "$certificate" -noout -pubkey) == "$(openssl pkey -in "$key" -pubout)" ]] ||
		fail "$key is not the key of $certificate"
}

edge_preflight() {
	log "checking the Cloudflare edge: published ranges, firewall, origin certificate"
	check_published_ranges
	"$dogfood_dir/firewall.sh" --check
	if has_new_origin_pair; then
		check_origin_certificate "$origin_certificate.new" "$origin_key.new"
	else
		check_origin_certificate "$origin_certificate" "$origin_key"
	fi
}

# Moves the new origin pair, which edge_preflight accepted, over the live one. Each rename is
# atomic; Caddy reads the files only when recreate_proxy_and_collector starts it again.
install_new_origin_pair() {
	has_new_origin_pair || return 0
	log "installing the new origin certificate and key"
	mv -f "$origin_key.new" "$origin_key"
	mv -f "$origin_certificate.new" "$origin_certificate"
}

compose_problem=$(bash "$script_dir/../docker/check-compose-version.sh" 2>&1) || fail "$compose_problem"
[[ -f $dogfood_dir/compose.yaml ]] || fail "no release directory $release"
[[ -f $release/frontend/dist/index.html ]] || fail "$release/frontend/dist has no web app build"
check_env_file
# A release from before the Cloudflare edge would serve certificates Caddy can no longer
# obtain behind the firewall, so neither deploys nor rollbacks accept one.
[[ -f $dogfood_dir/cloudflare-ips.txt ]] || fail "$release predates the Cloudflare edge"
# The `-` operand reads standard input; BSD paste requires an operand.
PF_CLOUDFLARE_IP_RANGES=$(cloudflare_ranges | paste -sd ' ' -)
export PF_CLOUDFLARE_IP_RANGES
export PF_IMAGE_TAG=$commit
load_environment
: "${PF_DOGFOOD_APP_HOST:?set PF_DOGFOOD_APP_HOST in $env_file}"
: "${PF_DOGFOOD_AUTH_HOST:?set PF_DOGFOOD_AUTH_HOST in $env_file}"

if [[ $mode == rollback ]]; then
	log "rolling the application back to $commit"
	verify_recorded_digests "${application_services[@]}"
	compose up --detach --wait --no-build --no-deps "${application_services[@]}"
	recreate_proxy_and_collector
	health_checks
	switch_current
	log "rolled back to $commit"
	exit 0
fi

: "${PF_SMOKE_USER_EMAIL:?set PF_SMOKE_USER_EMAIL in $env_file}"
edge_preflight
install_new_origin_pair

log "building images for $commit once"
compose --profile tasks build --pull
mkdir -p "$(dirname "$digests")"
for name in "${built_images[@]}"; do
	digest=$(image_digest "$name") || fail "no property-focus-$name:$commit image after the build"
	printf '%s\t%s\t%s\t%s\n' "$(date -u +%FT%TZ)" "$commit" "$name" "$digest" >>"$digests"
done

log "pulling pinned images"
compose --profile tasks pull --ignore-buildable --quiet

log "starting Postgres and preparing roles and databases"
compose up --detach --wait postgres
compose --profile tasks run --rm --no-deps db-init

log "migrating the schema (one controlled task)"
compose --profile tasks run --rm --no-deps migrate

log "starting Keycloak (imports the realm on first start) and reconciling the realm"
compose up --detach --wait --no-build keycloak
realm apply
realm check
realm ensure-user --email "$PF_SMOKE_USER_EMAIL"

log "starting the application and the proxy"
verify_recorded_digests "${built_images[@]}"
compose up --detach --wait --no-build --remove-orphans
recreate_proxy_and_collector
health_checks
switch_current
log "deployed $commit"
