"""Install the AW1000 Internet settings UI and eligibility backend from audited sources."""
import json
from pathlib import Path

RUNTIME = r'''#!/bin/sh
# AW1000 configuration and SIM eligibility. IPv4 rules remain owned by mwan3.
set -eu
umask 077
RUN=${AW1000_INTERNET_RUN:-/var/run/aw1000-internet}
PENDING=${AW1000_PENDING:-/etc/aw1000-internet-pending}
CONFIG=${AW1000_CONFIG_ROOT:-/etc/config}
mkdir -p "$RUN"
get() { uci -q get "$1" || true; }
valid() { case "$1" in ''|*[!A-Za-z0-9_]*) return 1;; esac; }
now() { cut -d. -f1 /proc/uptime; }
generation() {
    port=$(get "qmodem.$1.at_port")
    case "$port" in /dev/ttyUSB[0-9]*) :;; *) echo missing; return;; esac
    path=$(readlink -f "${AW1000_SYS_ROOT:-/sys}/class/tty/${port##*/}/device" 2>/dev/null) || path=''
    while [ -n "$path" ] && [ "$path" != / ]; do
        if [ -r "$path/devnum" ] && [ -r "$path/busnum" ]; then
            printf '%s:%s:%s\n' "$path" "$(cat "$path/busnum")" "$(cat "$path/devnum")"
            return
        fi
        path=${path%/*}
    done
    echo missing
}
classify() {
    response=$(printf '%s' "$1" | tr -d '\r')
    if printf '%s\n' "$response" | grep -qE '^\+CPIN: READY[[:space:]]*$'; then echo ready
    elif printf '%s\n' "$response" | grep -qE '^\+CPIN: (SIM PIN|SIM PUK|PH-)|^\+CME ERROR: (11|12)[[:space:]]*$'; then echo locked
    elif printf '%s\n' "$response" | grep -qiE '^\+CPIN: NOT INSERTED|^\+CME ERROR: (10|SIM not inserted)[[:space:]]*$'; then echo absent
    elif printf '%s\n' "$response" | grep -qiE '^\+CME ERROR: (13|SIM failure)[[:space:]]*$'; then echo failed
    else echo unknown; fi
}
publish() {
    previous=$(jq -r '[.modem,.state]|join(":")' "$RUN/sim.json" 2>/dev/null || true)
    state=$(classify "$(printf '%s' "$1" | tr -d '\r' | sed '/^[[:space:]]*$/d')")
    jq -cn --arg modem "$2" --arg state "$state" --argjson time "$(now)" --arg generation "$(generation "$2")" \
        '{modem:$modem,state:$state,time:$time,generation:$generation}' > "$RUN/sim.tmp.$$"
    mv "$RUN/sim.tmp.$$" "$RUN/sim.json"
    case "$state" in ready) ready=1;; *) ready=0;; esac
    dir="/var/run/qmodem/${2}_dir"
    [ ! -d "$dir" ] || printf '%s\n' "$ready" > "$dir/aw1000-sim-ready"
    [ "$previous" = "$2:$state" ] || touch "$RUN/dirty"
}
sim_state() {
    modem=${1:-$(get mwan3.globals.aw1000_modem)}
    valid "$modem" || { echo unknown; return 0; }
    jq -r --arg modem "$modem" --argjson now "$(now)" --arg generation "$(generation "$modem")" \
        'if .modem==$modem and $generation!="missing" and .generation==$generation and ($now-.time)>=0 and ($now-.time)<=20 then .state else "unknown" end' \
        "$RUN/sim.json" 2>/dev/null || echo unknown
}
monitor() {
    while :; do
        modem=$(get mwan3.globals.aw1000_modem)
        if valid "$modem" && [ "$(get mwan3.globals.aw1000_enabled)" = 1 ]; then
            port=$(get "qmodem.$modem.at_port")
            case "$port" in /dev/ttyUSB[0-9]*)
                # Same command queue as the telephone backend; no direct serial I/O.
                before=$(generation "$modem")
                response=$(/usr/sbin/aw1000-phone-at "$port" 'AT+CPIN?' 2>/dev/null) || response=''
                [ "$before" = "$(generation "$modem")" ] || response=''
                publish "$response" "$modem"
                ;;
            esac
        fi
        sleep 5
    done
}
gate() {
    old=$(cat "$RUN/blocked" 2>/dev/null || true)
    applied=$(cat "$RUN/gate-applied" 2>/dev/null || true)
    if [ "$(get mwan3.globals.aw1000_enabled)" != 1 ]; then
        rm -f "$RUN/blocked" "$RUN/gate-applied"
        if valid "$old"; then /usr/sbin/mwan3 ifup "$old"; fi
        return 0
    fi
    cell=$(get mwan3.globals.aw1000_cellular)
    valid "$cell" || return 0
    state=$(sim_state)
    case "$state" in
        absent|locked|failed)
            printf '%s\n' "$cell" > "$RUN/blocked.tmp"; mv "$RUN/blocked.tmp" "$RUN/blocked"
            if [ "$applied" != "block:$cell" ]; then
                /usr/sbin/mwan3 ifdown "$cell"
                echo "block:$cell" > "$RUN/gate-applied"
                logger -t aw1000-internet "Cellular excluded: SIM $state"
            fi;;
        ready)
            if [ -n "$old" ] || [ "$applied" = "block:$cell" ] || [ "$applied" = "recover:$cell" ]; then
                if [ "$applied" != "recover:$cell" ]; then
                    # Keep the hard gate closed while native tracking restarts.
                    /usr/sbin/mwan3 ifup "$cell"
                    echo "recover:$cell" > "$RUN/gate-applied"
                    now > "$RUN/recovery-start"
                    rm -f "$RUN/recovery-seen"
                    logger -t aw1000-internet 'SIM ready; waiting for native IPv4 recovery checks'
                else
                    track="${AW1000_MWAN_TRACK:-/var/run/mwan3track}/$cell"
                    status=$(cat "$track/STATUS" 2>/dev/null || echo unknown)
                    case "$status" in offline|connecting|disabled) touch "$RUN/recovery-seen";; esac
                    last=$(cat "$track/TIME" 2>/dev/null || echo 0)
                    case "$last" in ''|*[!0-9]*) last=0;; esac
                    start=$(cat "$RUN/recovery-start" 2>/dev/null || now)
                    if [ "$status" = online ] && [ -f "$RUN/recovery-seen" ] && [ "$last" -ge "$start" ] && [ "$last" -le "$(now)" ] && [ "$(( $(now)-last ))" -le 10 ]; then
                        rm -f "$RUN/blocked"
                        # Persist release intent before retryable publication.
                        echo "release:$cell" > "$RUN/gate-applied"
                        /usr/sbin/mwan3 ifup "$cell"
                        rm -f "$RUN/gate-applied" "$RUN/recovery-seen" "$RUN/recovery-start"
                    fi
                fi
            elif [ "$applied" = "release:$cell" ]; then
                /usr/sbin/mwan3 ifup "$cell"
                rm -f "$RUN/gate-applied" "$RUN/recovery-seen" "$RUN/recovery-start"
            fi;;
        *)
            # Retry an interrupted exclusion even when a later AT query is busy.
            if [ "$old" = "$cell" ] && [ "$applied" != "block:$cell" ] && [ "$applied" != "recover:$cell" ]; then
                /usr/sbin/mwan3 ifdown "$cell"
                echo "block:$cell" > "$RUN/gate-applied"
            fi;;
    esac
}
reload_network() {
    ubus call network reload >/dev/null
    /etc/init.d/mwan3 reload
    touch "$RUN/dirty"
}
rollback() {
    [ -d "$PENDING" ] || return 0
    for name in network mwan3 aw1000 qmodem; do
        cp "$PENDING/$name" "$CONFIG/$name"
    done
    rm -rf "$PENDING"
    reload_network
    logger -t aw1000-internet 'Unconfirmed settings rolled back'
}
sync_routes() {
    [ "$(get mwan3.globals.aw1000_enabled)" = 1 ] || return 0
    priority=$(get aw1000.internet.priority)
    case "$priority" in wired|cellular|wired_only|cellular_only) :;; *) return 0;; esac
    changed=0
    cell=$(get mwan3.globals.aw1000_cellular)
    for iface in wan "$cell"; do
        valid "$iface" || continue
        [ "$(get "network.$iface")" = interface ] || continue
        # Keep both per-interface routes for native tracking and bound diagnostics.
        # Only aw_failover membership limits the normal Internet exit.
        if [ "$(get "network.$iface.defaultroute")" != 1 ]; then
            uci set "network.$iface.defaultroute=1"
            changed=1
        fi
    done
    if [ "$changed" = 1 ]; then
        uci commit network
        if [ "${1:-}" != --defer ]; then ubus call network reload >/dev/null; fi
    fi
}
tick() {
    exec 8>"$RUN/apply.lock"; flock -n 8 || return 0
    if [ -f "$PENDING/deadline" ]; then
        if [ "$(cat "$PENDING/boot" 2>/dev/null || true)" != "$(cat /proc/sys/kernel/random/boot_id)" ] || [ "$(now)" -ge "$(cat "$PENDING/deadline")" ]; then rollback; fi
    fi
    sync_routes
    gate
}
status() {
    cell=$(get mwan3.globals.aw1000_cellular)
    wired=$(ubus call network.interface.wan status 2>/dev/null) || wired='{}'
    cellular='{}'; if valid "$cell"; then cellular=$(ubus call "network.interface.$cell" status 2>/dev/null) || cellular='{}'; fi
    jq -cn --arg priority "$(get aw1000.internet.priority)" --arg mode "$(get network.wan.proto)" \
        --arg username "$(get network.wan.username)" --arg vlan "$(get aw1000.internet.vlan)" --arg mtu "$(get network.wan.mtu)" \
        --arg selected "$(cat /var/run/aw1000-mwan-follow/selected 2>/dev/null || true)" \
        --arg sim "$(sim_state)" --arg blocked "$(cat "$RUN/blocked" 2>/dev/null || true)" \
        --arg wan_health "$(cat /var/run/mwan3track/wan/STATUS 2>/dev/null || echo unknown)" \
        --arg cell_health "$(cat "/var/run/mwan3track/$cell/STATUS" 2>/dev/null || echo unknown)" \
        --argjson ipv6 "$(cat /var/run/aw1000-ipv6-follow/status.json 2>/dev/null || echo '{}')" \
        --arg dns_choice "$(cut -d '|' -f1 /var/run/aw1000-mwan-follow/dns-signature 2>/dev/null || true)" \
        --argjson wired "$wired" --argjson cellular "$cellular" \
        --argjson last "$(cat /var/run/aw1000-mwan-follow/event.json 2>/dev/null || echo '{}')" \
        --argjson pending "$(if [ -d "$PENDING" ]; then echo true; else echo false; fi)" \
        '{priority:$priority,mode:$mode,username:$username,vlan:$vlan,mtu:$mtu,selected:$selected,sim:$sim,blocked:$blocked,wan_health:$wan_health,cell_health:$cell_health,ipv6:$ipv6,dns_choice:$dns_choice,wired_errors:($wired.errors//[]),wired_up:($wired.up//false),cellular_up:($cellular.up//false),last:$last,pending:$pending}'
}
apply() {
    input=$(cat)
    # No eval, shell sourcing or generated UCI batch: credentials remain literal arguments.
    printf '%s' "$input" | jq -e '
        (.priority=="wired" or .priority=="cellular" or .priority=="wired_only" or .priority=="cellular_only") and (.mode=="dhcp" or .mode=="pppoe") and
        ([.username,.password,.vlan,.mtu]|all(type=="string" and length<=128 and (test("[\\x00-\\x1f\\x7f]")|not))) and
        (.mode!="pppoe" or (.username|length)>0) and
        (.vlan=="" or (.vlan|test("^[0-9]{1,4}$") and (tonumber>=1 and tonumber<=4094))) and
        (.mtu=="" or (.mtu|test("^[0-9]{3,4}$") and (tonumber>=1280 and tonumber<=1500))) and
        (.mode!="pppoe" or .mtu=="" or (.mtu|tonumber)<=1492)' >/dev/null || { echo '{"error":"Invalid input; check the PPPoE username, VLAN and MTU"}'; return 0; }
    exec 8>"$RUN/apply.lock"; flock -x 8
    if [ -d "$PENDING" ] && [ ! -f "$PENDING/deadline" ]; then rm -rf "$PENDING"; fi
    [ ! -d "$PENDING" ] || { echo '{"error":"Confirm the previous settings or wait for rollback"}'; return 0; }
    # Refuse uncommitted/manual policy changes rather than silently replacing them.
    for config in network mwan3 aw1000 qmodem; do
        [ -z "$(uci changes "$config")" ] || { echo '{"error":"Apply or revert pending changes on other pages first"}'; return 0; }
    done
    case "$(get aw1000.internet.priority)" in
        wired_only) expected_members=aw_wired;;
        cellular_only) expected_members=aw_cell;;
        wired|cellular|'') expected_members='aw_wired aw_cell';;
        *) expected_members=unsupported;;
    esac
    if ! { [ "$(get mwan3.globals.aw1000_enabled)" = 1 ] &&
    [ "$(get mwan3.aw_default.family)" = ipv4 ] &&
    [ "$(get mwan3.aw_default.dest_ip)" = 0.0.0.0/0 ] &&
    [ "$(get mwan3.aw_failover.last_resort)" = unreachable ] &&
    [ "$(get mwan3.aw_default.use_policy)" = aw_failover ] && \
    [ "$(get mwan3.aw_failover.use_member)" = "$expected_members" ] && \
    [ "$(get mwan3.aw_wired.interface)" = wan ]; }; then
        echo '{"error":"Custom policy detected; restore the AW1000 default policy first"}'
        return 0
    fi
    extra=$(uci -q show mwan3 | sed -n 's/^mwan3\.\([A-Za-z0-9_]*\)=rule$/\1/p' | sed '/^aw_default$/d')
    [ -z "$extra" ] || { echo '{"error":"Additional mwan3 rules detected; check policy compatibility in advanced settings"}'; return 0; }
    mode=$(printf '%s' "$input" | jq -r .mode)
    user=$(printf '%s' "$input" | jq -r .username)
    pass=$(printf '%s' "$input" | jq -r .password)
    if [ "$mode" = pppoe ] && [ -z "$pass" ] && [ -z "$(get network.wan.password)" ]; then echo '{"error":"A password is required for the initial PPPoE configuration"}'; return 0; fi
    priority=$(printf '%s' "$input" | jq -r .priority)
    vlan=$(printf '%s' "$input" | jq -r '.vlan|if .=="" then . else tonumber|tostring end')
    mtu=$(printf '%s' "$input" | jq -r '.mtu|if .=="" then . else tonumber|tostring end')
    base=$(get aw1000.internet.base_device)
    [ -n "$base" ] || base=$(get network.wan.device)
    case "$base" in ''|*[!A-Za-z0-9_.:-]*) echo '{"error":"Unsupported WAN device configuration"}'; return 0;; esac
    mkdir "$PENDING"
    for name in network mwan3 aw1000 qmodem; do cp "$CONFIG/$name" "$PENDING/$name"; done
    # Deadline is installed before mutation; the running service restores even if RPC is interrupted.
    cat /proc/sys/kernel/random/boot_id > "$PENDING/boot"
    echo "$(( $(now)+120 ))" > "$PENDING/deadline"
    uci set aw1000.internet=policy
    uci set "aw1000.internet.priority=$priority"
    uci set "aw1000.internet.base_device=$base"
    uci set "aw1000.internet.vlan=$vlan"
    uci set "network.wan.proto=$mode"
    if [ "$mode" = pppoe ]; then
        uci set "network.wan.username=$user"
        [ -z "$pass" ] || uci set "network.wan.password=$pass"
        uci set network.wan.ipv6=1
    fi
    if [ -n "$vlan" ]; then
        uci set network.aw_wan_vlan=device
        uci set network.aw_wan_vlan.type=8021q
        uci set "network.aw_wan_vlan.ifname=$base"
        uci set "network.aw_wan_vlan.vid=$vlan"
        uci set "network.aw_wan_vlan.name=$base.$vlan"
        uci set "network.wan.device=$base.$vlan"
    else
        uci set "network.wan.device=$base"
        uci -q delete network.aw_wan_vlan || true
    fi
    if [ -n "$mtu" ]; then uci set "network.wan.mtu=$mtu"; else uci -q delete network.wan.mtu || true; fi
    if [ "$priority" = cellular ] || [ "$priority" = cellular_only ]; then wired_metric=2; cell_metric=1; else wired_metric=1; cell_metric=2; fi
    uci -q delete mwan3.aw_failover.use_member || true
    case "$priority" in
        wired_only) uci add_list mwan3.aw_failover.use_member=aw_wired;;
        cellular_only) uci add_list mwan3.aw_failover.use_member=aw_cell;;
        *) uci add_list mwan3.aw_failover.use_member=aw_wired
           uci add_list mwan3.aw_failover.use_member=aw_cell;;
    esac
    uci set "mwan3.aw_wired.metric=$wired_metric"
    uci set "mwan3.aw_cell.metric=$cell_metric"
    # Main-table metrics match selected preference for unmarked router traffic.
    uci set "network.wan.metric=$((wired_metric*10))"
    cell=$(get mwan3.globals.aw1000_cellular)
    if valid "$cell" && [ "$(get "network.$cell")" = interface ]; then uci set "network.$cell.metric=$((cell_metric*10))"; fi
    modem=$(get mwan3.globals.aw1000_modem)
    if valid "$modem"; then uci set "qmodem.$modem.metric=$((cell_metric*10))"; fi
    for name in network mwan3 aw1000 qmodem; do uci commit "$name"; done
    sync_routes --defer
    reload_network
    echo '{"ok":true,"timeout":120}'
}
case "${1:-}" in
    --classify) classify "$(cat)";;
    --sim-result) valid "${2:-}" && publish "$(cat)" "$2";;
    --monitor) monitor;;
    --tick) tick;;
    --sim-state) sim_state "${2:-}";;
    --status) status;;
    --rpc)
        case "${2:-}" in
            list) echo '{"status":{},"apply":{"priority":"","mode":"","username":"","password":"","vlan":"","mtu":""},"confirm":{}}';;
            call)
                case "${3:-}" in
                    status) status;;
                    apply) apply;;
                    confirm) exec 8>"$RUN/apply.lock"; flock -x 8; rm -rf "$PENDING"; echo '{"ok":true}';;
                esac;;
        esac;;
    *) exit 2;;
esac
'''

TRANSLATIONS = {'SIM ready': 'SIM 就绪', 'No SIM card': '未插入 SIM', 'SIM locked': 'SIM 需要解锁', 'SIM failure': 'SIM 故障', 'SIM status unknown': 'SIM 状态未知', 'Wired network only': '仅有线网络', 'Cellular network only': '仅蜂窝网络', 'Prefer wired': '有线优先', 'Prefer cellular': '蜂窝优先', 'DHCP (automatic)': 'DHCP 自动获取', 'PPPoE': 'PPPoE 宽带拨号', 'Leave blank to keep the saved password': '留空保留已保存密码', 'Leave blank for no VLAN': '留空不使用 VLAN', 'Automatic (usually 1492 for PPPoE)': '自动（PPPoE 通常为 1492）', 'Keep current settings': '确认保留当前设置', 'Online': '正常', 'Unavailable': '不可用', 'Checking recovery': '恢复检测中', 'Checking failure': '故障检测中', 'Unknown': '未知', 'Wired': '有线', 'Cellular': '蜂窝', 'Offline': '离线', 'Native IPv6': '原生 IPv6', 'IPv6 unavailable on the current uplink': '当前出口无可用 IPv6', 'Waiting for synchronization': '等待同步', 'Authentication failed; check the PPPoE credentials': '宽带认证失败，请检查账号密码', 'Dial-up connection failed': '拨号连接失败', 'No wired link detected': '未检测到有线链路', 'Current uplink: ': '当前出口：', 'Wired: ': '有线：', 'Interface connected': '接口已连接', 'Interface disconnected': '接口未连接', '; Cellular: ': '；蜂窝：', 'Data session connected': '数据会话已连接', 'Data session disconnected': '数据会话未连接', 'Excluded by SIM status': 'SIM 状态已排除该线路', 'Following the uplink': '已跟随出口', 'Waiting for uplink synchronization': '等待出口同步', '; Wired error: ': '；有线错误：', 'Last switch: ': '最近切换：', 'None': '暂无', '; Reason: ': '；原因：', 'PPPoE username': 'PPPoE 账号', 'PPPoE password': 'PPPoE 密码', 'Settings were not applied; refresh to check the confirmation status': '设置未应用，请刷新页面检查待确认状态', 'Applying settings. Check connectivity and confirm within 120 seconds to prevent automatic rollback.': '正在应用。请确认网络可用；120 秒内未确认会自动恢复原设置。', 'Keep settings': '确认保留设置', 'Confirm Internet settings': '确认上网设置', 'Check connectivity. Automatic rollback in ': '请确认网络可用，剩余 ', ' seconds.': ' 秒自动回退。', 'Save and apply': '保存并应用', 'Internet access': '上网方式', 'Priority modes switch uplinks on failure. Single-uplink modes use only the selected uplink. Health checks use IPv4; IPv6 and DNS follow the active uplink.': '双线优先模式会在故障时切换；仅有线网络／仅蜂窝模式固定使用所选线路。仅通过 IPv4 探测判断健康，IPv6 与 DNS 跟随实际出口。', 'Wired connection type': '有线上网方式', 'Advanced settings': '高级设置', 'Wired VLAN ID (1-4094)': '有线 VLAN ID（1–4094）', 'Wired MTU': '有线 MTU', 'Single-uplink modes do not fail over. Both uplinks keep their sessions, routes, IPv4 checks and IPv6 clients active; normal Internet traffic follows the selected mode.': '仅有线网络／仅蜂窝模式不会切换到另一线路。两路均保留拨号、路由及 IPv4 检测，IPv6 客户端保持在线；普通上网出口跟随所选模式。', 'Probe targets: 223.5.5.5 and 119.29.29.29. A response from either target counts as success. Recovery requires consecutive successes to avoid flapping. Existing connections may need to reconnect after the public address changes.': '探测目标：223.5.5.5、119.29.29.29，任一个响应即视为本轮成功。恢复需连续通过检测，避免反复切换。切换公网地址时，已有连接可能需要重建。', 'Invalid input; check the PPPoE username, VLAN and MTU': '输入无效，请检查 PPPoE 账号、VLAN 和 MTU', 'Confirm the previous settings or wait for rollback': '请先确认或等待上一次设置回退', 'Apply or revert pending changes on other pages first': '请先应用或撤销其他页面未保存的配置', 'Custom policy detected; restore the AW1000 default policy first': '检测到自定义策略，请先恢复 AW1000 默认策略', 'Additional mwan3 rules detected; check policy compatibility in advanced settings': '检测到额外 mwan3 规则，请先在高级页面确认策略兼容性', 'A password is required for the initial PPPoE configuration': '首次配置 PPPoE 必须填写密码', 'Unsupported WAN device configuration': 'WAN 设备配置不受支持', 'IPv6: ': 'IPv6：', '; DNS: ': '；DNS：'}

UI = r''' 'use strict';
'require view';
'require rpc';
'require poll';
'require ui';
var status = rpc.declare({object:'aw1000.internet',method:'status',reject:true,expect:{}});
var apply = rpc.declare({object:'aw1000.internet',method:'apply',params:['priority','mode','username','password','vlan','mtu'],reject:true,expect:{}});
var confirm = rpc.declare({object:'aw1000.internet',method:'confirm',reject:true,expect:{}});
return view.extend({
    handleSaveApply:null, handleSave:null, handleReset:null,
    load:function(){return status();},
    render:function(data){
        var labels={ready:_("SIM ready"),absent:_("No SIM card"),locked:_("SIM locked"),failed:_("SIM failure"),unknown:_("SIM status unknown")};
        function input(value,type){return E('input',{class:'cbi-input-text',type:type||'text',value:value||''});}
        var priority=E('select',{class:'cbi-input-select'},[E('option',{value:'wired_only'},_("Wired network only")),E('option',{value:'cellular_only'},_("Cellular network only")),E('option',{value:'wired'},_("Prefer wired")),E('option',{value:'cellular'},_("Prefer cellular"))]);
        priority.value=data.priority||'wired_only';
        var mode=E('select',{class:'cbi-input-select'},[E('option',{value:'dhcp'},_("DHCP (automatic)")),E('option',{value:'pppoe'},_("PPPoE"))]);
        mode.value=data.mode==='pppoe'?'pppoe':'dhcp';
        var user=input(data.username), password=input('','password'), vlan=input(data.vlan), mtu=input(data.mtu);
        password.placeholder=_("Leave blank to keep the saved password"); vlan.placeholder=_("Leave blank for no VLAN"); mtu.placeholder=_("Automatic (usually 1492 for PPPoE)");
        var retain=E('button',{class:'cbi-button cbi-button-positive',style:'display:none',click:function(){confirm().then(function(){retain.style.display='none';});}},_("Keep current settings"));
        var info=E('div',{}), error=E('div',{class:'alert-message warning',style:'display:none'});
        function health(s){return {online:_("Online"),offline:_("Unavailable"),connecting:_("Checking recovery"),disconnecting:_("Checking failure")}[s]||_("Unknown");}
        function refresh(s){
            retain.style.display=s.pending?'':'none';
            var line=s.selected==='wan'?_("Wired"):s.selected?_("Cellular"):_("Offline");
            var event=s.last||{};
            var v6=s.ipv6||{};
            var v6text=v6.choice===s.selected?({native:_("Native IPv6"),nat66:'IPv6 NAT66',unavailable:_("IPv6 unavailable on the current uplink"),none:_("IPv6 unavailable on the current uplink")}[v6.mode]||v6.mode||_("Waiting for synchronization")):_("Waiting for synchronization");
            var errors=(s.wired_errors||[]).map(function(e){return {AUTH_FAILED:_("Authentication failed; check the PPPoE credentials"),AUTH_TOPEER_FAILED:_("Authentication failed; check the PPPoE credentials"),CONNECT_FAILED:_("Dial-up connection failed"),NO_CARRIER:_("No wired link detected")}[e.code]||e.code;}).join('、');
            domContent(info,[E('p',{},_("Current uplink: ")+line+'；'+(labels[s.sim]||labels.unknown)),
                E('p',{},_("Wired: ")+(s.wired_up?_("Interface connected"):_("Interface disconnected"))+' / '+health(s.wan_health)+_("; Cellular: ")+(s.cellular_up?_("Data session connected"):_("Data session disconnected"))+' / '+(s.blocked?_("Excluded by SIM status"):health(s.cell_health))),
                E('p',{},_("IPv6: ")+v6text+_("; DNS: ")+(s.selected&&s.dns_choice===s.selected?_("Following the uplink"):_("Waiting for uplink synchronization"))+(errors?_("; Wired error: ")+errors:'')),
                E('p',{},_("Last switch: ")+(event.time||_("None"))+_("; Reason: ")+(event.reason||_("None")))]);
        }
        function domContent(node,children){while(node.firstChild)node.removeChild(node.firstChild);children.forEach(function(c){node.appendChild(c);});}
        function row(label,field){return E('div',{class:'cbi-value'},[E('label',{class:'cbi-value-title'},label),E('div',{class:'cbi-value-field'},field)]);}
        var userRow=row(_("PPPoE username"),user), passRow=row(_("PPPoE password"),password);
        function toggle(){userRow.style.display=passRow.style.display=mode.value==='pppoe'?'':'none';}
        mode.addEventListener('change',toggle);toggle();refresh(data);
        poll.add(function(){return status().then(refresh);},3);
        var save=E('button',{class:'cbi-button cbi-button-apply',click:ui.createHandlerFn(this,function(){
            error.style.display='none';
            return apply(priority.value,mode.value,user.value,password.value,vlan.value,mtu.value).then(function(result){
                if(!result || result.ok!==true)throw new Error(result&&result.error&&_(result.error)||_("Settings were not applied; refresh to check the confirmation status"));
                var remain=120;
                var msg=E('p',{},_("Applying settings. Check connectivity and confirm within 120 seconds to prevent automatic rollback."));
                var button=E('button',{class:'cbi-button cbi-button-positive',click:function(){confirm().then(function(){clearInterval(timer);ui.hideModal();password.value='';}).catch(function(e){msg.textContent=e.message;});}},_("Keep settings"));
                ui.showModal(_("Confirm Internet settings"),[msg,button]);
                var timer=setInterval(function(){remain--;msg.textContent=_("Check connectivity. Automatic rollback in ")+remain+_(" seconds.");if(remain<=0){clearInterval(timer);ui.hideModal();}},1000);
            }).catch(function(e){error.textContent=_(e.message);error.style.display='';});
        })},_("Save and apply"));
        return E('div',{},[E('h2',{},_("Internet access")),E('p',{},_("Priority modes switch uplinks on failure. Single-uplink modes use only the selected uplink. Health checks use IPv4; IPv6 and DNS follow the active uplink.")),info,retain,error,
            row(_("Internet access"),priority),row(_("Wired connection type"),mode),userRow,passRow,
            E('details',{},[E('summary',{},_("Advanced settings")),row(_("Wired VLAN ID (1-4094)"),vlan),row(_("Wired MTU"),mtu)]),
            E('p',{},_("Single-uplink modes do not fail over. Both uplinks keep their sessions, routes, IPv4 checks and IPv6 clients active; normal Internet traffic follows the selected mode.")),
            E('p',{},_("Probe targets: 223.5.5.5 and 119.29.29.29. A response from either target counts as success. Recovery requires consecutive successes to avoid flapping. Existing connections may need to reconnect after the public address changes.")),save]);
    }
});
'''


def install(tree):
    files = {
        'usr/sbin/aw1000-internet': RUNTIME,
        'usr/libexec/rpcd/aw1000.internet': '#!/bin/sh\nexec /usr/sbin/aw1000-internet --rpc "$@"\n',
        'www/luci-static/resources/view/aw1000/internet.js': UI,
        'etc/hotplug.d/iface/99-aw1000-follow': '#!/bin/sh\nmkdir -p /var/run/aw1000-internet\ntouch /var/run/aw1000-internet/dirty\n',
        'usr/share/luci/menu.d/aw1000-internet.json': json.dumps({'admin/network/aw1000_internet': {'title':'Internet access','order':15,'action':{'type':'view','path':'aw1000/internet'},'depends':{'acl':['aw1000-internet']}}},ensure_ascii=False),
        'usr/share/rpcd/acl.d/aw1000-internet.json': json.dumps({'aw1000-internet':{'description':'AW1000 Internet settings','read':{'ubus':{'aw1000.internet':['status']}},'write':{'ubus':{'aw1000.internet':['apply','confirm']}}}}),
    }
    for name, content in files.items():
        target = Path(tree)/'files'/name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(content)
        target.chmod(0o755 if content.startswith('#!/bin/sh') else 0o644)

    package = Path(tree)/'package/custom/luci-i18n-aw1000-internet-zh-cn'
    package.mkdir(parents=True, exist_ok=True)
    (package/'aw1000-internet.zh-cn.po').write_text(
        'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n' +
        ''.join('msgid '+json.dumps(en,ensure_ascii=False)+'\nmsgstr '+json.dumps(cn,ensure_ascii=False)+'\n\n'
                for en,cn in sorted(TRANSLATIONS.items())))
    (package/'Makefile').write_text(r"""include $(TOPDIR)/rules.mk
PKG_NAME:=luci-i18n-aw1000-internet-zh-cn
PKG_VERSION:=1.10.0
PKG_RELEASE:=1
PKG_BUILD_DEPENDS:=luci-base/host
include $(INCLUDE_DIR)/package.mk

define Package/luci-i18n-aw1000-internet-zh-cn
  SECTION:=luci
  CATEGORY:=LuCI
  TITLE:=AW1000 Internet access Chinese translation
  DEPENDS:=+luci-base
  PKGARCH:=all
endef

define Build/Compile
	$(STAGING_DIR_HOSTPKG)/bin/po2lmo ./aw1000-internet.zh-cn.po $(PKG_BUILD_DIR)/aw1000-internet.zh-cn.lmo
endef

define Package/luci-i18n-aw1000-internet-zh-cn/install
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/i18n
	$(INSTALL_DATA) $(PKG_BUILD_DIR)/aw1000-internet.zh-cn.lmo $(1)/usr/lib/lua/luci/i18n/
endef

$(eval $(call BuildPackage,luci-i18n-aw1000-internet-zh-cn))
""")

if __name__ == '__main__':
    import sys
    install(Path(sys.argv[1]))
