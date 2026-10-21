#!/bin/sh
# The RG500Q-EA data link was verified on hardware to require host Raw-IP.
# Run before each managed QMI dial attempt, including after USB re-enumeration.
set -eu
[ "$#" -eq 2 ] || exit 2
instance=$1
device=$2
case "$instance" in ''|*[!A-Za-z0-9_]*) exit 2;; esac
case "$device" in ''|*[!A-Za-z0-9_.:-]*) exit 2;; esac
[ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = 'arcadyan,aw1000' ] || exit 0
name=$(uci -q get "qmodem.$instance.name" | tr '[:upper:]' '[:lower:]')
[ "$name" = rg500q-ea ] || exit 0
[ "$(uci -q get "qmodem.$instance.data_interface")" = usb ] || exit 0
[ "$(uci -q get "qmodem.$instance.host_ipv4_owner")" = netifd ] || exit 0
base="/sys/class/net/$device"
driver=$(readlink -f "$base/device/driver" 2>/dev/null || true)
[ "${driver##*/}" = qmi_wwan ] || exit 0
mode=$(cat "$base/qmi/raw_ip") || exit 1
case "$mode" in Y|1) exit 0;; N|0) :;; *) exit 1;; esac
flags=$(cat "$base/flags") || exit 1
was_up=$((flags & 1))
# qmi_wwan rejects a format change while the link is up. Restore the previous
# link state even when the write/verification fails; netifd owns IPv4 setup.
trap 'if [ "$was_up" = 1 ]; then ip link set dev "$device" up; fi' EXIT
ip link set dev "$device" down
printf 'Y\n' > "$base/qmi/raw_ip"
[ "$(cat "$base/qmi/raw_ip")" = Y ]
logger -t aw1000-qmi "Enabled host Raw-IP on $device before managed dialing"
