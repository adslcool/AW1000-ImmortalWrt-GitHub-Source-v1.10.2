#!/bin/sh
# QModem recovery consumes mwan3's tracker; never probe through business policy.
# 0 healthy/enrolled, 1 unhealthy, 2 not owned, 3 defer while unknown/stale.
set -u
if [ "${1:-}" = enrolled ]; then mode=enrolled; section=${2:-}; else mode=health; section=${1:-}; fi
[ "$(uci -q get mwan3.globals.aw1000_enabled)" = 1 ] || exit 2
[ "$(uci -q get mwan3.globals.aw1000_modem)" = "$section" ] || exit 2
[ "$mode" != enrolled ] || exit 0
if [ -x /usr/sbin/aw1000-internet ]; then
    sim=$(/usr/sbin/aw1000-internet --status | jq -r '.sim')
    [ "$sim" = ready ] || exit 3
fi
iface=$(uci -q get mwan3.globals.aw1000_cellular) || exit 3
case "$iface" in ''|*[!A-Za-z0-9_]*) exit 3;; esac
[ "$(cat /var/run/aw1000-internet/blocked 2>/dev/null)" != "$iface" ] || exit 3
RUN=${AW1000_MWAN_TRACK:-/var/run/mwan3track}
pid=$(cat "$RUN/$iface/PID" 2>/dev/null) || exit 3
case "$pid" in ''|*[!0-9]*) exit 3;; esac
kill -0 "$pid" 2>/dev/null || exit 3
last=$(cat "$RUN/$iface/TIME" 2>/dev/null) || exit 3
case "$last" in ''|*[!0-9]*) exit 3;; esac
now=$(cut -d. -f1 /proc/uptime)
[ "$now" -ge "$last" ] && [ "$((now-last))" -le 60 ] || exit 3
case "$(cat "$RUN/$iface/STATUS" 2>/dev/null)" in
    online|disconnecting) exit 0;;
    offline) exit 1;;
    *) exit 3;;
esac
