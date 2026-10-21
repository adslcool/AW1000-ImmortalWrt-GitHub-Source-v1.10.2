#!/bin/sh
# Applies only on a fresh installation / factory reset. The marker is backed up
# by sysupgrade, so an upgrade which keeps settings keeps the user's settings.
set -eu

CONF=/etc/aw1000-build.conf
MARKER=/etc/aw1000-provisioned

save_marker() {
    touch /etc/sysupgrade.conf
    grep -qxF "$MARKER" /etc/sysupgrade.conf || printf '%s\n' "$MARKER" >> /etc/sysupgrade.conf
    printf '%s\n' 'AW1000 provisioning v1' > "$MARKER"
    chmod 600 "$MARKER"
}

apply_config() {
    if [ -f "$MARKER" ]; then
        rm -f "$CONF"
        return 0
    fi
    # At the early uci-defaults stage these archives still exist if sysupgrade
    # restored settings, including migration from a firmware without our marker.
    if [ -f /sysupgrade.tgz ] || [ -f /tmp/sysupgrade.tar ]; then
        save_marker
        rm -f "$CONF"
        logger -t aw1000-firstboot "Preserved restored sysupgrade settings"
        return 0
    fi
    [ -f "$CONF" ] || return 1
    . "$CONF"

    [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = "arcadyan,aw1000" ] || {
        logger -t aw1000-firstboot "Unexpected board; provisioning stopped"
        return 1
    }

    # Keep credentials and LAN usable even if the Wi-Fi PHYs appear late.
    shadow_tmp=$(mktemp /etc/shadow.aw1000.XXXXXX)
    awk -F: -v hash="$ROOT_PASSWORD_HASH" 'BEGIN { OFS=":" } $1=="root" { $2=hash } { print }' \
        /etc/shadow > "$shadow_tmp"
    chmod 600 "$shadow_tmp"
    mv "$shadow_tmp" /etc/shadow
    uci set "network.lan.ipaddr=$LAN_IP"
    uci set "network.lan.netmask=$NETMASK"
    uci set 'network.lan.proto=static'
    uci set 'system.@system[0].hostname=AW1000'
    uci set 'system.@system[0].timezone=CST-8'
    uci set 'system.@system[0].zonename=Asia/Shanghai'
    uci set 'luci.main.mediaurlbase=/luci-static/argon'
    uci commit network
    uci commit system
    uci commit luci

    /sbin/wifi config || return 1
    radio24=''
    radio5=''
    radios=$(uci -q show wireless | sed -n 's/^wireless\.\([^=]*\)=wifi-device$/\1/p')
    for radio in $radios; do
        band=$(uci -q get "wireless.$radio.band" || true)
        case "$band" in
            2g) radio24="$radio" ;;
            5g) radio5="$radio" ;;
        esac
    done
    # Do not assume radio0 / radio1 order. A deferred service retries if missing.
    [ -n "$radio24" ] && [ -n "$radio5" ] || return 1

    ifaces=$(uci -q show wireless | sed -n 's/^wireless\.\([^=]*\)=wifi-iface$/\1/p')
    for iface in $ifaces; do
        device=$(uci -q get "wireless.$iface.device" || true)
        case "$device" in
            "$radio24"|"$radio5") uci delete "wireless.$iface" ;;
        esac
    done

    uci set "wireless.$radio24.disabled=0"
    uci set "wireless.$radio24.country=$COUNTRY_CODE"
    uci set "wireless.$radio24.channel=auto"
    uci set "wireless.$radio24.htmode=HE20"
    uci set "wireless.$radio5.disabled=0"
    uci set "wireless.$radio5.country=$COUNTRY_CODE"
    uci set "wireless.$radio5.channel=149"
    uci set "wireless.$radio5.htmode=HE80"

    uci set 'wireless.aw1000_2g=wifi-iface'
    uci set "wireless.aw1000_2g.device=$radio24"
    uci set 'wireless.aw1000_2g.mode=ap'
    uci set 'wireless.aw1000_2g.network=lan'
    uci set "wireless.aw1000_2g.ssid=$SSID_24G"
    uci set 'wireless.aw1000_2g.encryption=psk2+ccmp'
    uci set "wireless.aw1000_2g.key=$WIFI_PASSWORD"
    uci set 'wireless.aw1000_2g.disabled=0'
    uci set 'wireless.aw1000_2g.wps_pushbutton=1'
    uci set 'wireless.aw1000_5g=wifi-iface'
    uci set "wireless.aw1000_5g.device=$radio5"
    uci set 'wireless.aw1000_5g.mode=ap'
    uci set 'wireless.aw1000_5g.network=lan'
    uci set "wireless.aw1000_5g.ssid=$SSID_5G"
    uci set 'wireless.aw1000_5g.encryption=psk2+ccmp'
    uci set "wireless.aw1000_5g.key=$WIFI_PASSWORD"
    uci set 'wireless.aw1000_5g.disabled=0'
    uci set 'wireless.aw1000_5g.wps_pushbutton=1'
    uci commit wireless

    # The onboarding worker waits for the provision marker; write intent first
    # so delayed Wi-Fi provisioning never restores the bootstrap hash afterwards.
    if [ "${DEVICE_INIT_ENABLED:-0}" = 1 ]; then
        mkdir -p /etc/aw1000-board-state
        chmod 700 /etc/aw1000-board-state
        touch /etc/aw1000-board-state/requested
        chmod 600 /etc/aw1000-board-state/requested
    fi
    save_marker
    rm -f "$CONF" /etc/uci-defaults/99-aw1000
    logger -t aw1000-firstboot "LAN, root password and both Wi-Fi bands configured"
    return 0
}

case "${1:-once}" in
    once) apply_config ;;
    retry)
        count=0
        while [ "$count" -lt 30 ]; do
            # Run in a separate shell so errors remain fatal with set -e.
            if "$0" once; then
                ubus call network reload >/dev/null 2>&1 || true
                exit 0
            fi
            count=$((count + 1))
            sleep 4
        done
        logger -t aw1000-firstboot "Wi-Fi provisioning incomplete; check logread and wifi status"
        exit 1
        ;;
    *) exit 2 ;;
esac
