#!/bin/sh
# AW1000 steady LEDs v1.7.5
# Keep system LEDs steady; kernel netdev handles links appearing after boot.
set -eu
BOARD=${AW1000_BOARD_FILE:-/tmp/sysinfo/board_name}
LEDS=${AW1000_LEDS_ROOT:-/sys/class/leds}
PROC=${AW1000_PROC_ROOT:-/proc}
STATUS=${AW1000_LED_STATUS:-/var/run/aw1000-leds.json}
if [ "${1:-}" = --status ]; then
    cat "$STATUS" 2>/dev/null || echo '{}'
    exit 0
fi
[ "$(cat "$BOARD" 2>/dev/null)" = arcadyan,aw1000 ] || exit 0
if [ "${1:-}" = --configure ]; then
    for section in $(uci -q show system | sed -n 's/^system\.\([^=]*\)=led$/\1/p'); do
        name=$(uci -q get "system.$section.sysfs" || true)
        if [ -n "$name" ] && [ ! -d "$LEDS/$name" ]; then
            uci delete "system.$section"
            continue
        fi
        trigger=$(uci -q get "system.$section.trigger" || true)
        case "$trigger" in
            netdev)
                mode=$(uci -q get "system.$section.mode" || true)
                mode=$(printf '%s\n' "$mode" | sed -e 's/\<tx\>//g' -e 's/\<rx\>//g' -e 's/  */ /g' -e 's/^ //' -e 's/ $//')
                [ -n "$mode" ] || mode="link"
                uci set "system.$section.mode=$mode"
                ;;
            timer|heartbeat|phy*rx|phy*tx|phy*tpt)
                uci set "system.$section.trigger=none"
                uci set "system.$section.default=0"
                ;;
        esac
    done
    uci set system.led_wifi=led
    uci set system.led_wifi.name=WiFi
    uci set system.led_wifi.sysfs=green:wifi
    uci set system.led_wifi.trigger=netdev
    uci set system.led_wifi.dev=phy0-ap0
    uci set system.led_wifi.mode="link"
    # A netdev link can stay up without a SIM. The session checker owns 5G.
    for section in $(uci -q show system | sed -n 's/^system\.\([^=]*\)=led$/\1/p'); do
        name=$(uci -q get "system.$section.sysfs" || true)
        case "$name" in green:5g|blue:5g|red:5g)
            uci set "system.$section.trigger=none"
            uci set "system.$section.default=0"
            uci -q delete "system.$section.dev" || true
            uci -q delete "system.$section.mode" || true
            ;;
        esac
    done
    uci commit system
    /etc/init.d/led restart
fi
# Follow either active AP, preferring 5 GHz. No static radio numbering required.
wifi=$(ubus call network.wireless status 2>/dev/null | jq -r '
 [to_entries[] | select(.value.up==true) | .value as $r |
  $r.interfaces[]? | select(.config.mode=="ap") |
  {dev:.ifname,rank:(if $r.config.band=="5g" then 0 else 1 end)}] |
 sort_by(.rank) | .[0].dev // empty') || wifi=''
case "$wifi" in ''|*[!A-Za-z0-9_.:-]*) wifi='';; esac
# Button-off is authoritative even while netifd still reports the old AP.
[ ! -f "${AW1000_WIFI_OFF_FILE:-/var/run/aw1000-wifi-off}" ] || wifi=''
led="$LEDS/green:wifi"
if [ -d "$led" ] && [ -n "$wifi" ]; then
    trigger=$(cat "$led/trigger")
    case "$trigger" in *'[netdev]'*) :;; *) echo netdev > "$led/trigger";; esac
    [ "$(cat "$led/device_name" 2>/dev/null)" = "$wifi" ] || printf '%s\n' "$wifi" > "$led/device_name"
    echo 1 > "$led/link"
elif [ -d "$led" ]; then
    echo none > "$led/trigger"
    echo 0 > "$led/brightness"
fi
# Also normalize LEDs reset by hotplug/vendor services. Do not force all colors
# on, and do not overwrite existing link-speed modes on the WAN PHY LED.
for led in "$LEDS"/*; do
    [ -d "$led" ] || continue
    trigger=$(cat "$led/trigger" 2>/dev/null) || continue
    case "$trigger" in
        *'[netdev]'*)
            for direction in rx tx; do
                [ ! -f "$led/$direction" ] || echo 0 > "$led/$direction"
            done
            ;;
        *'[timer]'*|*'[heartbeat]'*|*'[phy0rx]'*|*'[phy0tx]'*|*'[phy1rx]'*|*'[phy1tx]'*)
            brightness=$(cat "$led/brightness")
            echo none > "$led/trigger"
            [ "$brightness" = 0 ] || brightness=1
            printf '%s\n' "$brightness" > "$led/brightness"
            ;;
    esac
done
# Cellular and Mobile signal lamps share the same data-session state.
# Never issue AT commands just to update a lamp.
bound_modem=$(uci -q get mwan3.globals.aw1000_modem || true)
modem=$bound_modem
modem_source=mwan3
case "$modem" in ''|*[!A-Za-z0-9_]*) modem='';; esac
selection_reason=no_modem
if [ -z "$modem" ]; then
    # Read-only fallback for firmware/upgrades without managed mwan3 enrolment.
    modem_source=none
    candidate_count=0
    detected=''
    for instance in $(uci -q show qmodem | sed -n 's/^qmodem\.\([A-Za-z0-9_]*\)=modem-device$/\1/p'); do
        name=$(uci -q get "qmodem.$instance.name" | tr '[:upper:]' '[:lower:]') || name=''
        [ "$name" = rg500q-ea ] || continue
        [ "$(uci -q get "qmodem.$instance.data_interface" || true)" = usb ] || continue
        candidate_alias=$(uci -q get "qmodem.$instance.alias" || true)
        case "$candidate_alias" in ''|-) candidate_alias=$instance;; esac
        case "$candidate_alias" in wan|lan|wan6|*[!A-Za-z0-9_]*) continue;; esac
        candidate_count=$((candidate_count+1))
        detected=$instance
    done
    if [ "$candidate_count" = 1 ]; then
        modem=$detected
        modem_source=qmodem_discovery
    elif [ "$candidate_count" -gt 1 ]; then selection_reason=ambiguous_modems; fi
fi
sim_ready=0
cell_online=0
sim_state=unknown
tracker_online=0
session_process=0
data_session=0
reason=$selection_reason
sim_source=unknown
alias=''
expected_device=''
case "$modem" in
    ''|*[!A-Za-z0-9_]*) :;;
    *)
        rundir="${AW1000_MODEM_RUN:-/var/run/qmodem}/${modem}_dir"
        sim_ready=$(cat "$rundir/aw1000-sim-ready" 2>/dev/null) || sim_ready=0
        helper=${AW1000_INTERNET_HELPER:-/usr/sbin/aw1000-internet}
        if [ -x "$helper" ]; then
            sim_state=$("$helper" --sim-state "$modem" 2>/dev/null) || sim_state=unknown
            sim_source=runtime
        elif [ "$sim_ready" = 1 ]; then sim_state=ready; sim_source=legacy_snapshot; fi
        # Older helpers ignore the optional instance argument and return unknown
        # when no mwan3 instance is registered. Preserve their existing SIM proof.
        if [ "$sim_state" = unknown ] && [ "$modem" != "$bound_modem" ] && [ "$sim_ready" = 1 ]; then
            sim_state=ready
            sim_source=legacy_snapshot
        fi
        alias=$(uci -q get "qmodem.$modem.alias" || true)
        case "$alias" in ''|-) alias=$modem;; esac
        # QModem's logical interface defaults to the instance; the netdev is separate.
        expected_device=$(uci -q get "network.$alias.device" || true)
        [ -n "$expected_device" ] || expected_device=$alias
        interface_owner=$(uci -q get "network.$alias.modem_config" || true)
        # Unknown CPIN is not proof of absence. A fresh native tracker can
        # establish that the existing data session is actually passing traffic.
        sim_usable=0
        case "$sim_state" in
            ready) sim_usable=1;;
            unknown)
                track="${AW1000_MWAN_TRACK:-/var/run/mwan3track}/$alias"
                track_pid=$(cat "$track/PID" 2>/dev/null) || track_pid=''
                last=$(cat "$track/TIME" 2>/dev/null) || last=0
                case "$track_pid:$last" in *[!0-9:]*|:*|*:) track_pid='';; esac
                uptime=$(cut -d. -f1 "$PROC/uptime")
                if [ -n "$track_pid" ] && kill -0 "$track_pid" 2>/dev/null &&
                   [ "$last" -le "$uptime" ] && [ "$((uptime-last))" -le 10 ] &&
                   [ "$(cat "$track/STATUS" 2>/dev/null)" = online ]; then
                    tracker_online=1
                    sim_usable=1
                fi;;
        esac
        blocked=$(cat "${AW1000_INTERNET_RUN:-/var/run/aw1000-internet}/blocked" 2>/dev/null) || blocked=''
        if [ "$sim_state" != ready ] && [ -n "$alias" ] && [ "$blocked" = "$alias" ]; then sim_usable=0; fi
        reason=process_unavailable
        pid=$(cat "$rundir/$modem.pid" 2>/dev/null) || pid=''
        start=$(cat "$rundir/cm.start" 2>/dev/null) || start=''
        case "$alias:$pid:$start" in
            *[!A-Za-z0-9_:]*|::*|*::*) :;;
            *)
                case "$pid:$start" in *[!0-9:]*|:|*:) pid='';; esac
                if [ -n "$alias" ] && [ -n "$pid" ] &&
                   [ "$(uci -q get "qmodem.$modem.enable_dial" || true)" != 0 ] &&
                   [ "$(uci -q get qmodem.main.enable_dial || true)" != 0 ] &&
                   kill -0 "$pid" 2>/dev/null &&
                   [ "$(awk '{print $22}' "$PROC/$pid/stat" 2>/dev/null)" = "$start" ]; then
                    session_process=1
                    reason=no_data_session
                    info=$(ubus call "network.interface.$alias" status 2>/dev/null) || info='{}'
                    if [ -z "$interface_owner" ] || [ "$interface_owner" = "$modem" ]; then owner_ok=true; else owner_ok=false; fi
                    if printf '%s' "$info" | jq -e --arg device "$expected_device" --argjson owner_ok "$owner_ok" '
                      $owner_ok and .up==true and .proto=="qmodem_runtime" and .l3_device==$device and
                      ($device|test("^[A-Za-z0-9_.:-]+$")) and
                      any(.["ipv4-address"][]?; .address!="0.0.0.0" and ((.address // "") | test("^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$"))) and
                      any(.route[]?; .mask==0 and .target=="0.0.0.0")' >/dev/null 2>&1; then
                        data_session=1
                        reason=sim_not_confirmed
                        if [ "$sim_usable" = 1 ]; then
                            cell_online=1
                            sim_ready=1
                            reason=data_online
                        fi
                    fi
                fi
                ;;
        esac
        ;;
esac
for cell_color in red blue green; do
    led="$LEDS/$cell_color:5g"
    [ -d "$led" ] || continue
    echo none > "$led/trigger"
    if [ "$cell_color" = green ]; then echo "$cell_online" > "$led/brightness"; else echo 0 > "$led/brightness"; fi
done
for signal in red green blue; do
    led="$LEDS/$signal:signal"
    [ -d "$led" ] || continue
    echo none > "$led/trigger"
    if [ "$signal" = green ]; then echo "$cell_online" > "$led/brightness"; else echo 0 > "$led/brightness"; fi
done

# Expose the gate decisions without issuing AT commands or altering routes.
jq -cn --arg modem "$modem" --arg alias "$alias" --arg sim "$sim_state" --arg reason "$reason" \
    --arg modem_source "$modem_source" --arg sim_source "$sim_source" --arg device "$expected_device" \
    --argjson process "$session_process" --argjson data "$data_session" \
    --argjson tracker "$tracker_online" --argjson lit "$cell_online" \
    '{version:"1.7.5",modem:$modem,modem_source:$modem_source,interface:$alias,device:$device,sim:$sim,sim_source:$sim_source,live_process:($process==1),data_session:($data==1),fresh_tracker_online:($tracker==1),cellular_led:($lit==1),signal_led:($lit==1),reason:$reason}' > "$STATUS.tmp.$$"
mv "$STATUS.tmp.$$" "$STATUS"
