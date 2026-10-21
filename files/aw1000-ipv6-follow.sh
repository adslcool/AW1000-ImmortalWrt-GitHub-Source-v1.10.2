#!/bin/sh
# Both DHCPv6 clients stay online. Only IPv4's published policy selects egress.
set -eu
RUN=${AW1000_IPV6_RUN:-/var/run/aw1000-ipv6-follow}
BOARD=${AW1000_BOARD_FILE:-/tmp/sysinfo/board_name}
[ "$(cat "$BOARD" 2>/dev/null)" = arcadyan,aw1000 ] || exit 0
mkdir -p "$RUN"
exec 8>"$RUN/lock"
flock -x 8
valid() { case "$1" in ''|*[!A-Za-z0-9_]*) return 1;; esac; }
config_set() {
    [ "$(uci -q get "$1" || true)" = "$2" ] && return 0
    uci set "$1=$2" || exit 1
    return 1
}
classes_set() {
    [ "$(uci -q get network.lan.ip6class || true)" = "$1" ] && return 0
    uci -q delete network.lan.ip6class || true
    for class in $1; do uci add_list "network.lan.ip6class=$class" || exit 1; done
    return 1
}
restore() {
    if [ -f "$RUN/rules-owned" ]; then
        for pref in 8893 8894 8895 8900 8901 8902; do while ip -6 rule del pref "$pref" 2>/dev/null; do :; done; done
        ip -6 route flush table 61000 || true
        nft delete table ip6 aw1000_v6 2>/dev/null || true
        rm -f "$RUN/rules-owned"
    fi
    if [ -f "$RUN/config-owned" ]; then
        classes=$(uci -q get aw1000.ipv6.original_classes || true)
        classes_set "$classes" || true
        uci commit network
        for key in ra_default ra_lifetime ra dhcpv6 ndp ra_slaac; do
            old=$(uci -q get "aw1000.ipv6.original_$key" || true)
            if [ "$old" = unset ] || [ -z "$old" ]; then
                uci -q delete "dhcp.lan.$key" || true
            else uci set "dhcp.lan.$key=$old"; fi
        done
        uci commit dhcp
        ubus call network reload '{}' >/dev/null
        /etc/init.d/odhcpd reload
    fi
    rm -f "$RUN/signature" "$RUN/selected" "$RUN/status.json" "$RUN/exemptions" "$RUN/config-owned"
}
[ "${1:-}" != --restore ] || { restore; exit 0; }
[ "$(uci -q get mwan3.globals.aw1000_enabled || true)" = 1 ] || exit 0
choice=${1:-}
rows=${2:-[]}
parents=$(printf '%s' "$rows" | jq -r '.[] | select(.enabled) | .target')
selected='{}'
selected_iface=''
exemptions=''
# Discover clients on every pass, including after USB re-enumeration. Never down
# a standby client. Respect explicit user disabled/auto=0 settings.
for parent in $parents; do
    valid "$parent" || continue
    if [ "$(uci -q get "qmodem.$parent" || true)" = modem-device ]; then
        alias=$(uci -q get "qmodem.$parent.alias" || true)
        valid "$alias" && parent=$alias
    fi
    for iface in "${parent}v6" "${parent}6" "${parent}_6"; do
        info=$(ubus call "network.interface.$iface" status 2>/dev/null) || continue
        [ "$(printf '%s' "$info" | jq -r '.proto // empty')" = dhcpv6 ] || continue
        [ "$(uci -q get "network.$iface.disabled" || true)" != 1 ] || continue
        [ "$(uci -q get "network.$iface.auto" || true)" != 0 ] || continue
        if [ "$(printf '%s' "$info" | jq -r '.up or .pending or .autostart')" != true ]; then
            ubus call "network.interface.$iface" up '{}' >/dev/null
        fi
        dev=$(printf '%s' "$info" | jq -r '.l3_device // .device // empty')
        case "$dev" in ''|*[!A-Za-z0-9_.:-]*) :;; *) exemptions="$exemptions
oif $dev";; esac
        for address in $(printf '%s' "$info" | jq -r '.["ipv6-address"][]?.address | select(test("^[23][0-9A-Fa-f:]+$"))'); do
            exemptions="$exemptions
from $address/128"
        done
        rm -f "$RUN/stopped-$iface"
        if [ "$parent" = "$choice" ] && [ "$(printf '%s' "$info" | jq -r '.up')" = true ]; then
            selected=$info
            selected_iface=$iface
            break
        fi
    done
done
# Validate all tokens before using them in an nft transaction.
plan=$(printf '%s' "$selected" | jq -cS --arg iface "$selected_iface" '
 def ip: type=="string" and test("^[0-9A-Fa-f:]+$") and contains(":");
 {iface:$iface,dev:(.l3_device // .device // ""),
  address:([.["ipv6-address"][]? | select((.preferred // 1)>0) | .address | select(ip) | select(test("^[23]"))][0] // ""),
  gateway:([.route[]? | select(.mask==0 and (.valid // 1)>0) | .nexthop | select(ip)][0] // ""),
  prefixes:([.["ipv6-prefix"][]? | select((.preferred // 1)>0 and (.valid // 1)>0) |
   select(.address|ip) | select(.mask>=1 and .mask<=64) |
   {address,mask,class:(.class // $iface)}] | sort_by(.address,.mask))}')
dev=$(printf '%s' "$plan" | jq -r '.dev')
address=$(printf '%s' "$plan" | jq -r '.address')
gateway=$(printf '%s' "$plan" | jq -r '.gateway')
case "$dev" in ''|*[!A-Za-z0-9_.:-]*) dev='';; esac
ready=0
[ -z "$dev" ] || [ -z "$address" ] || [ -z "$gateway" ] || ready=1
ula=$(uci -q get network.globals.ula_prefix || true)
case "$ula" in [fF][cCdD]*/*) :;; *) ula='';; esac
if ! printf '%s' "$ula" | grep -Eq '^[fF][cCdD][0-9a-fA-F:]+/[0-9]{1,3}$'; then ula=''; fi
prefixes=$(printf '%s' "$plan" | jq -r '.prefixes[] | "\(.address)/\(.mask)"')
mode=unavailable
[ "$ready" = 0 ] || { if [ -n "$prefixes" ]; then mode=native; elif [ -n "$ula" ] && [ "$(uci -q get aw1000.ipv6.nat66_fallback || echo 1)" = 1 ]; then mode=nat66; fi; }
classes=local
if [ "$mode" = native ]; then
    for class in $(printf '%s' "$plan" | jq -r '.prefixes[].class' | sort -u); do
        valid "$class" && classes="$classes $class"
    done
fi
exemptions=$(printf '%s\n' "$exemptions" | sed '/^$/d' | sort -u)
signature="$choice|$mode|$ula|$plan|$exemptions"
# Recreate after external nft flushes; unchanged state never reloads netifd.
if [ "$(cat "$RUN/signature" 2>/dev/null)" = "$signature" ] &&
   nft list table ip6 aw1000_v6 >/dev/null 2>&1 &&
   ip -6 rule show | grep -q '^8902:' &&
   ip -6 route show table 61000 | grep -q '^unreachable default'; then exit 0; fi
if [ ! -f "$RUN/rules-owned" ]; then
    if ip -6 rule show | grep -Eq '^(889[345]|890[012]):'; then
        logger -t aw1000-ipv6 'Reserved IPv6 rule priority occupied; refusing to overwrite'
        exit 1
    fi
    touch "$RUN/rules-owned"
fi
rules=$(ip -6 rule show)
printf '%s\n' "$rules" | grep -q '^8893:' || ip -6 rule add pref 8893 to ff00::/8 lookup main
printf '%s\n' "$rules" | grep -q '^8900:' || ip -6 rule add pref 8900 lookup main suppress_prefixlength 0
printf '%s\n' "$rules" | grep -q '^8901:' || ip -6 rule add pref 8901 lookup 61000
printf '%s\n' "$rules" | grep -q '^8902:' || ip -6 rule add pref 8902 unreachable
# DHCPv6 maintenance and explicitly interface-bound diagnostics must work on
# standby too. /128 exemptions cover only the router's own upstream addresses,
# never a whole standby prefix advertised to LAN.
if [ "$(cat "$RUN/exemptions" 2>/dev/null)" != "$exemptions" ]; then
    for pref in 8894 8895; do while ip -6 rule del pref "$pref" 2>/dev/null; do :; done; done
    printf '%s\n' "$exemptions" | while read -r kind value; do
        [ -n "$value" ] || continue
        case "$kind" in
            oif) ip -6 rule add pref 8895 oif "$value" lookup main;;
            from) ip -6 rule add pref 8894 from "$value" lookup main;;
        esac
    done
    printf '%s\n' "$exemptions" > "$RUN/exemptions"
fi
# Independent table: no source-rule fallback onto the standby default route.
# A final unreachable rule also covers the small table-update window.
# Materialize the IPv6 table first: flushing a nonexistent table can fail.
ip -6 route replace unreachable default table 61000 metric 32767
ip -6 route flush table 61000
ip -6 route add unreachable default table 61000 metric 32767
if [ "$ready" = 1 ]; then
    ip -6 route add table 61000 fe80::/64 dev "$dev"
    if [ "$gateway" = :: ]; then
        ip -6 route add table 61000 default dev "$dev" src "$address" metric 10
    else ip -6 route add table 61000 default via "$gateway" dev "$dev" src "$address" metric 10; fi
fi
lan=$(ubus call network.interface.lan status | jq -r '.l3_device // "br-lan"')
case "$lan" in ''|*[!A-Za-z0-9_.:-]*) exit 1;; esac
{
    if nft list table ip6 aw1000_v6 >/dev/null 2>&1; then echo 'delete table ip6 aw1000_v6'; fi
    echo 'table ip6 aw1000_v6 {'
    echo 'chain forward_guard { type filter hook forward priority -5; policy accept;'
    printf 'iifname "%s" oifname "%s" return\n' "$lan" "$lan"
    printf 'iifname "%s" ip6 daddr { fc00::/7, fe80::/10, ff00::/8 } return\n' "$lan"
    if [ "$mode" != unavailable ]; then
        # Old deprecated public sources must never leave through the new ISP.
        for prefix in $prefixes; do
            printf 'iifname "%s" oifname "%s" ip6 saddr %s return\n' "$lan" "$dev" "$prefix"
        done
        if [ "$mode" = nat66 ]; then
            printf 'iifname "%s" oifname "%s" ip6 saddr %s return\n' "$lan" "$dev" "$ula"
        fi
    fi
    printf 'iifname "%s" reject with icmpv6 type admin-prohibited\n' "$lan"
    echo '}'
    if [ "$mode" = nat66 ]; then
        echo 'chain fallback_nat { type nat hook postrouting priority srcnat; policy accept;'
        printf 'oifname "%s" ip6 saddr %s snat to %s\n' "$dev" "$ula" "$address"
        echo '}'
    fi
    echo '}'
} > "$RUN/policy.nft"
nft -f "$RUN/policy.nft"
touch "$RUN/config-owned"
network_changed=0
classes_set "$classes" || network_changed=1
if [ "$network_changed" = 1 ]; then
    uci commit network
    ubus call network reload '{}' >/dev/null
fi
ra=0
[ "$mode" = unavailable ] || ra=1800
changed=0
config_set dhcp.lan.ra_default 2 || changed=1
config_set dhcp.lan.ra_lifetime "$ra" || changed=1
if [ "$changed" = 1 ]; then uci commit dhcp; /etc/init.d/odhcpd reload; fi
# Remove only LAN IPv6 conntracks after an actual line/address/mode change.
if [ -f "$RUN/signature" ]; then
    if [ -f "$RUN/flow-prefixes" ]; then
        while IFS= read -r prefix; do
            [ -n "$prefix" ] || continue
            conntrack -D -f ipv6 -s "$prefix" >/dev/null 2>&1 || true
        done < "$RUN/flow-prefixes"
    fi
fi
{ printf '%s\n' "$prefixes"; [ -z "$ula" ] || printf '%s\n' "$ula"; } > "$RUN/flow-prefixes"
printf '%s\n' "$signature" > "$RUN/signature"
printf '%s\n' "$choice" > "$RUN/selected"
printf '%s' "$plan" | jq --arg choice "$choice" --arg mode "$mode" '{choice:$choice,mode:$mode,upstream:.,note:"IPv4 policy only; both DHCPv6 clients stay online"}' > "$RUN/status.json"
logger -t aw1000-ipv6 "Selected $choice IPv6 mode=$mode; DHCPv6 hot standby retained"
