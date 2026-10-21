#!/bin/sh
# Observe the actual published nft policy. No probes or IPv4 route modifications.
set -eu
RUN=${AW1000_MWAN_RUN:-/var/run/aw1000-mwan-follow}
mkdir -p "$RUN"
valid() { case "$1" in ''|*[!A-Za-z0-9_]*) return 1;; esac; }
cleanup() {
    changed=0
    /usr/sbin/aw1000-ipv6-follow --restore || true
    if [ -f "$RUN/dns-files" ]; then
        while IFS= read -r f; do
            case "$f" in /tmp/dnsmasq*.d/aw1000-mwan.conf) [ ! -f "$f" ] || { rm -f "$f"; changed=1; };; esac
        done < "$RUN/dns-files"
    fi
    rm -f "$RUN/dns-files" "$RUN/dns-signature"
    [ "$changed" = 0 ] || /etc/init.d/dnsmasq restart
}
[ "${1:-}" != --restore ] || { cleanup; exit 0; }
once() {
    [ "$(uci -q get mwan3.globals.aw1000_enabled || true)" = 1 ] || { cleanup; return 0; }
    mask=$(uci -q get mwan3.globals.mmx_mask || true)
    case "$mask" in 0x00ff0000|0xff0000) :;; *) logger -t aw1000-mwan 'Unexpected mwan3 mark mask; IPv6 selection not changed'; return 1;; esac
    choice=''
    # Pure failover has a direct mark assignment followed by last-resort mark.
    # Read the first assignment from the installed chain, not tracker counters.
    policy=$(nft -nn list chain inet mwan3 mwan3_policy_aw_failover 2>/dev/null) || policy=''
    mark=$(printf '%s\n' "$policy" | sed -n -e 's/.*meta mark set .* [|^] \(0x[0-9a-fA-F]*\).*/\1/p' -e 's/.*meta mark set .* xor \(0x[0-9a-fA-F]*\).*/\1/p' | head -n 1)
    case "$mark" in ''|*[!0-9a-fA-Fx]*) id=0;; *) id=$((mark >> 16));; esac
    index=0
    for iface in $(uci -q show mwan3 | sed -n 's/^mwan3\.\([A-Za-z0-9_]*\)=interface$/\1/p'); do
        index=$((index+1))
        if [ "$index" = "$id" ]; then choice=$iface; fi
    done
    cell=$(uci -q get mwan3.globals.aw1000_cellular || true)
    if [ "$choice" != wan ] && [ "$choice" != "$cell" ]; then choice=''; fi
    # Keep both clients online; a separate IPv6 policy controls LAN forwarding.
    rows='[{"enabled":true,"target":"wan"}]'
    if valid "$cell"; then rows=$(printf '%s' "$rows" | jq -c --arg cell "$cell" '.+[{enabled:true,target:$cell}]'); fi
    previous=$(cat "$RUN/selected" 2>/dev/null || echo startup)
    if [ "$previous" != "$choice" ]; then
        reason='IPv4 健康检测或线路状态变化'
        blocked=$(cat /var/run/aw1000-internet/blocked 2>/dev/null || true)
        if [ -n "$blocked" ] && [ "$previous" = "$blocked" ]; then reason='SIM 卡不可用'; fi
        [ ! -d /etc/aw1000-internet-pending ] || reason='上网设置变更'
        [ -n "$choice" ] || reason='双线路均不可用'
        jq -cn --arg time "$(date '+%Y-%m-%d %H:%M:%S %z')" --arg reason "$reason" \
            --arg from "$previous" --arg to "$choice" '{time:$time,reason:$reason,from:$from,to:$to}' > "$RUN/event.json.tmp"
        mv "$RUN/event.json.tmp" "$RUN/event.json"
        logger -t aw1000-mwan "Exit changed: $previous -> $choice ($reason)"
    fi
    printf '%s\n' "$choice" > "$RUN/selected"
    /usr/sbin/aw1000-ipv6-follow "$choice" "$rows" || return 1
    info=$(ubus call "network.interface.$choice" status 2>/dev/null) || info='{}'
    dns=$(printf '%s' "$info" | jq -r '.["dns-server"][]? | select(test("^[0-9.]+$"))')
    if [ "$(uci -q get "network.$choice.peerdns" || true)" = 0 ]; then dns=$(uci -q get "network.$choice.dns" || true); fi
    [ -n "$dns" ] || dns='223.5.5.5 119.29.29.29'
    # Add only the selected line's IPv6 resolvers, never standby DNS.
    if [ -n "$choice" ]; then
        for v6 in "${choice}v6" "${choice}6" "${choice}_6"; do
            v6info=$(ubus call "network.interface.$v6" status 2>/dev/null) || continue
            if [ "$(printf '%s' "$v6info" | jq -r '.up // false')" = true ]; then
                if [ "$(uci -q get "network.$v6.peerdns" || true)" = 0 ]; then
                    v6dns=$(uci -q get "network.$v6.dns" || true)
                else v6dns=$(printf '%s' "$v6info" | jq -r '.["dns-server"][]? | select(contains(":"))'); fi
                dns="$dns $v6dns"
                break
            fi
        done
    fi
    signature="$choice|$dns"
    [ "$(cat "$RUN/dns-signature" 2>/dev/null)" != "$signature" ] || return 0
    : > "$RUN/dns-files"
    for section in $(uci -q show dhcp | sed -n 's/^dhcp\.\([^=]*\)=dnsmasq$/\1/p'); do
        [ "$(uci -q get "dhcp.$section.disabled" || true)" != 1 ] || continue
        dir=$(uci -q get "dhcp.$section.confdir" || true)
        [ -n "$dir" ] || dir="/tmp/dnsmasq.$section.d"
        case "$dir" in /tmp/dnsmasq*.d) :;; *) continue;; esac
        mkdir -p "$dir"
        { echo no-resolv; for server in $dns; do
            case "$server" in ''|*[!0-9a-fA-F:.]*) continue;; esac
            printf 'server=%s\n' "$server"
        done; } > "$dir/aw1000-mwan.conf.tmp"
        mv "$dir/aw1000-mwan.conf.tmp" "$dir/aw1000-mwan.conf"
        printf '%s\n' "$dir/aw1000-mwan.conf" >> "$RUN/dns-files"
    done
    /etc/init.d/dnsmasq restart
    printf '%s\n' "$signature" > "$RUN/dns-signature"
}
if [ "${1:-}" = --once ]; then
    exec 9>"$RUN/lock"
    flock -n 9 || exit 0
    /usr/sbin/aw1000-leds
    once
    exit $?
fi
exec 9>"$RUN/lock"
flock -n 9 || exit 0
trap 'exit 0' INT TERM
trap cleanup EXIT
ticks=0
while :; do
    /usr/sbin/aw1000-internet --tick || logger -t aw1000-internet 'Eligibility/config reconciliation deferred'
    if [ "$((ticks % 2))" = 0 ]; then /usr/sbin/aw1000-leds || true; fi
    if [ -f /var/run/aw1000-internet/dirty ] || [ "$((ticks % 15))" = 0 ]; then
        rm -f /var/run/aw1000-internet/dirty
        once || { logger -t aw1000-mwan 'IPv6/DNS sync deferred'; touch /var/run/aw1000-internet/dirty; }
    fi
    ticks=$((ticks+1))
    sleep 1 & wait $! || true
done
