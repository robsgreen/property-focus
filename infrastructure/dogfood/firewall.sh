#!/usr/bin/env bash
# Admits inbound TCP 80 and 443 on the dogfood host only from Cloudflare's published ranges,
# cloudflare-ips.txt next to this script, and drops those ports from every other source (A5).
# Port 22 and all other traffic keep the rules they have.
#
# Usage, run by an operator as root with the user's explicit approval (docs/operations.md):
#   firewall.sh            apply the rules, then print them
#   firewall.sh --check    exit 1, naming the difference, when the live rules are not exactly
#                          what `firewall.sh` applies; prints nothing on success
#
# For each address family (iptables for IPv4, ip6tables for IPv6) the script keeps the chain
# PF-CLOUDFLARE-EDGE, which returns for a source in a Cloudflare range and drops the rest, and
# puts jumps into it first in two chains:
# - DOCKER-USER, which Docker-published ports pass through: Docker forwards them to Caddy's
#   container, so they bypass INPUT and any ufw rule. Its jumps match the connection's
#   original destination port, because the packet's own port is already translated.
# - INPUT, for connections the host itself accepts on 80 or 443 (Docker's userland proxy).
# Every jump matches only packets that arrive on the external interface (the default route's,
# or PF_FIREWALL_INTERFACE), so loopback health checks and traffic between containers pass.
# The chain is replaced atomically, and each jump is inserted once: reruns are harmless.
#
# Rules live in the kernel and are lost on reboot; docs/operations.md runs this script at
# boot. `--check` reads the rules as root, or as the deploy user through sudo rules that allow
# exactly `iptables -S` and `ip6tables -S`.

set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ranges_file=$here/cloudflare-ips.txt
chain=PF-CLOUDFLARE-EDGE

fail() {
	echo "firewall: $*" >&2
	exit 1
}

usage() {
	echo "usage: $0 [--check]" >&2
	exit 2
}

mode=apply
case ${1:-} in
'') ;;
--check) mode=check ;;
*) usage ;;
esac

[[ -r $ranges_file ]] || fail "cannot read $ranges_file"

interface=${PF_FIREWALL_INTERFACE:-$(ip -4 route show default 2>/dev/null | awk '{ for (i = 1; i < NF; i++) if ($i == "dev") { print $(i + 1); exit } }')}
[[ -n $interface ]] || fail "no default route; set PF_FIREWALL_INTERFACE to the public interface"

# The CIDRs of one family (4 or 6) from cloudflare-ips.txt.
family_ranges() {
	local cidr
	while read -r cidr _; do
		[[ -z $cidr || $cidr == \#* ]] && continue
		if [[ ($1 == 4 && $cidr != *:*) || ($1 == 6 && $cidr == *:*) ]]; then
			echo "$cidr"
		fi
	done <"$ranges_file"
}

# The chain's rules for one family, as `iptables -S` prints them.
chain_rules() {
	local cidr
	while read -r cidr; do
		echo "-A $chain -s $cidr -j RETURN"
	done < <(family_ranges "$1")
	echo "-A $chain -j DROP"
}

# The jumps that lead the parent chain $1, as `iptables -S` prints them.
jump_rules() {
	case $1 in
	DOCKER-USER)
		echo "-A DOCKER-USER -i $interface -p tcp -m conntrack --ctorigdstport 80 --ctdir ORIGINAL -j $chain"
		echo "-A DOCKER-USER -i $interface -p tcp -m conntrack --ctorigdstport 443 --ctdir ORIGINAL -j $chain"
		;;
	INPUT)
		echo "-A INPUT -i $interface -p tcp -m multiport --dports 80,443 -j $chain"
		;;
	esac
}

# Prints the `-A` rules of chain $2 from the `-S` listing of command $1, in order.
live_rules() {
	local listing
	if ((EUID == 0)); then
		listing=$("$1" -w -S)
	else
		listing=$(sudo -n "$1" -S) || fail "cannot read the $1 rules: run as root, or allow the deploy user 'sudo $1 -S'"
	fi
	grep -E -- "^-A $2 " <<<"$listing" || true
}

has_chain() {
	if ((EUID == 0)); then
		"$1" -w -S "$2" >/dev/null 2>&1
	else
		sudo -n "$1" -S 2>/dev/null | grep -qxE -- "-N $2|-P $2 .*"
	fi
}

# Replaces the chain's rules in one transaction, then makes the jumps the first rules of each
# parent chain that exists.
apply_family() {
	local command=$1 family=$2 parent rule position
	{
		echo "*filter"
		echo ":$chain - [0:0]"
		chain_rules "$family"
		echo "COMMIT"
	} | "$command-restore" -w --noflush
	for parent in DOCKER-USER INPUT; do
		if ! has_chain "$command" "$parent"; then
			echo "IPv$family: no $parent chain; nothing to guard there"
			continue
		fi
		[[ $(live_rules "$command" "$parent" | head -n "$(jump_rules "$parent" | wc -l)") == "$(jump_rules "$parent")" ]] && continue
		while read -r rule; do
			read -ra rule <<<"${rule#-A "$parent" }"
			while "$command" -w -D "$parent" "${rule[@]}" 2>/dev/null; do :; done
		done < <(jump_rules "$parent")
		position=1
		while read -r rule; do
			read -ra rule <<<"${rule#-A "$parent" }"
			"$command" -w -I "$parent" "$position" "${rule[@]}"
			position=$((position + 1))
		done < <(jump_rules "$parent")
	done
}

# Prints one line per difference between the live rules of a family and the expected ones.
check_family() {
	local command=$1 family=$2 parent expected
	if [[ $(live_rules "$command" "$chain") != "$(chain_rules "$family")" ]]; then
		echo "IPv$family: chain $chain does not hold exactly the ranges of $ranges_file"
	fi
	for parent in DOCKER-USER INPUT; do
		has_chain "$command" "$parent" || continue
		expected=$(jump_rules "$parent")
		if [[ $(live_rules "$command" "$parent" | head -n "$(wc -l <<<"$expected")") != "$expected" ]]; then
			echo "IPv$family: the first rules of $parent are not the jumps to $chain for ports 80 and 443 on $interface"
		fi
	done
}

if [[ $mode == check ]]; then
	problems=$(
		check_family iptables 4
		check_family ip6tables 6
	)
	[[ -z $problems ]] || fail "the host admits ports 80 or 443 from outside Cloudflare's ranges; run firewall.sh as root:
$problems"
	exit 0
fi

((EUID == 0)) || fail "run as root"
apply_family iptables 4
apply_family ip6tables 6
for command in iptables ip6tables; do
	echo "# $command"
	for parent in "$chain" DOCKER-USER INPUT; do
		live_rules "$command" "$parent"
	done
done
