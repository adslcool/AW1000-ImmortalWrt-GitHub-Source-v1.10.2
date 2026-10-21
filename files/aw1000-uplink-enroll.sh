#!/bin/sh
# Called by QModem's post-scan hook, and once at boot for existing instances.
# This configures the dialer; it never changes USB composition or AT port IDs.
set -eu
[ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = 'arcadyan,aw1000' ] || exit 1
[ "$(uci -q get mwan3.globals.aw1000_enabled || true)" = 1 ] || exit 0

enable_ipv6() {
    [ "$(uci -q get "qmodem.$section.aw1000_ipv6_initialized" || true)" != 1 ] || return 0
    # IPv6 uses netifd/odhcp6c normally; only IPv4 participates in failover.
    uci set "qmodem.$section.pdp_type=ipv4v6"
    uci set "qmodem.$section.extend_prefix=1"
    uci set "qmodem.$section.ra_master=0"
    # Supply missing LAN defaults without replacing an existing IPv6 policy.
    if [ -z "$(uci -q get network.lan.ip6assign || true)" ]; then
        uci set 'network.lan.ip6assign=64'
    fi
    if [ "$(uci -q get dhcp.lan || true)" = dhcp ]; then
        for pair in ra:server dhcpv6:server ra_slaac:1; do
            option=${pair%%:*}
            if [ -z "$(uci -q get "dhcp.lan.$option" || true)" ]; then
                uci set "dhcp.lan.$option=${pair#*:}"
            fi
        done
    fi
    uci commit network
    uci commit dhcp
    uci set "qmodem.$section.aw1000_ipv6_initialized=1"
    uci commit qmodem
    # Old builds already have the uplink marker. Migrate them too, and apply
    # dual stack to an existing dial worker only once, respecting enable_dial.
    if [ -s "/var/run/qmodem/${section}_dir/$section.pid" ]; then
        reconfigure_instances="$reconfigure_instances $section"
    fi
    changed=1
    logger -t aw1000-ipv6 "Enabled dual-stack PDP and IPv6 prefix extension for $section"
}

enroll() {
    section=$1
    case "$section" in ''|*[!A-Za-z0-9_]*) return 2;; esac
    [ "$(uci -q get "qmodem.$section" || true)" = modem-device ] || return 0
    name=$(uci -q get "qmodem.$section.name" | tr '[:upper:]' '[:lower:]')
    [ "$name" = rg500q-ea ] || return 0
    [ "$(uci -q get "qmodem.$section.data_interface" || true)" = usb ] || return 0
    target=$(uci -q get mwan3.globals.aw1000_modem || true)
    if [ -n "$target" ] && [ "$target" != "$section" ]; then
        logger -t aw1000-failover "Backup already bound; additional RG500Q-EA left unchanged"
        return 0
    fi
    enable_ipv6
    if [ "$(uci -q get "qmodem.$section.aw1000_uplink_initialized" || true)" != 1 ]; then
    uci set "qmodem.$section.enable_dial=1"
    uci set "qmodem.$section.metric=200"
    uci set "qmodem.$section.use_ubus=1"
    uci set "qmodem.$section.host_ipv4_owner=netifd"
    uci set "qmodem.$section.en_bridge=0"
    uci set "qmodem.$section.monitor_enabled=1"
    uci set "qmodem.$section.monitor_profile=network_recovery"
    uci set "qmodem.$section.monitor_recovery_action=redial"
    uci set "qmodem.$section.monitor_primary_ip=223.5.5.5"
    uci set "qmodem.$section.monitor_backup_ip=119.29.29.29"
    uci set "qmodem.$section.monitor_interval=30"
    uci set "qmodem.$section.monitor_timeout=2"
    uci set "qmodem.$section.monitor_failure_packets=3"
    uci set "qmodem.$section.monitor_packet_interval=10"
    uci set "qmodem.$section.monitor_settle_timeout=90"
    uci set "qmodem.$section.aw1000_uplink_initialized=1"
    uci commit qmodem
    changed=1
    fi
    alias=$(uci -q get "qmodem.$section.alias" || true)
    case "$alias" in ''|-) alias=$section;; esac
    case "$alias" in wan|lan|wan6|*[!A-Za-z0-9_]*) return 0;; esac
    [ "$(uci -q get mwan3.globals.aw1000_cellular || true)" != "$alias" ] || return 0
    old_alias=$(uci -q get mwan3.globals.aw1000_cellular || true)
    if [ -n "$old_alias" ]; then uci -q delete "mwan3.$old_alias" || true; fi
    uci set "mwan3.$alias=interface"
    for pair in enabled:1 initial_state:offline family:ipv4 track_method:ping reliability:1 count:1 timeout:1 interval:2 failure_interval:2 recovery_interval:2 down:2 up:15; do
        uci set "mwan3.$alias.${pair%%:*}=${pair#*:}"
    done
    uci -q delete "mwan3.$alias.track_ip" || true
    uci add_list "mwan3.$alias.track_ip=223.5.5.5"
    uci add_list "mwan3.$alias.track_ip=119.29.29.29"
    uci set "mwan3.aw_cell.interface=$alias"
    uci set "mwan3.globals.aw1000_modem=$section"
    uci set "mwan3.globals.aw1000_cellular=$alias"
    uci commit mwan3
    changed=1
    logger -t aw1000-failover "Enrolled RG500Q-EA: wired preferred, cellular always dialed"
}

mkdir -p /var/lock
exec 9>/var/lock/qmodem-config.lock
flock -x 9
changed=0
reconfigure_instances=''
case "${1:-all}" in
    all)
        sections=$(uci -q show qmodem | sed -n 's/^qmodem\.\([^=]*\)=modem-device$/\1/p')
        for id in $sections; do enroll "$id"; done
        ;;
    *) enroll "$1" ;;
esac
flock -u 9
exec 9>&-
for instance in $reconfigure_instances; do
    /etc/init.d/qmodem_network reconfigure "$instance"
done
if [ "$changed" = 1 ]; then
    # Reload only when enrolling or migrating; ordinary scans do not restart PDP.
    /etc/init.d/mwan3 reload
    /etc/init.d/qmodem_monitor reload
    [ ! -x /etc/init.d/odhcpd ] || /etc/init.d/odhcpd reload
fi
