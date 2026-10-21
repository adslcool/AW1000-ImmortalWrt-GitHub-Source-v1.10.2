#!/usr/bin/env python3
"""Small, guarded integration changes on the pinned upstream source files."""
import hashlib
from pathlib import Path
import re

SSR_MARK = 0x5250
SSR_MASK = 0x0000FFFF
UPLINK_MASK = 0x00FF0000


def ssr_mark_patch(original):
    if 'FWMARK="0x5250"' in original and 'fwmark ${FWMARK}/0x0000ffff' in original:
        return original
    if original.count('FWMARK="0x53535250"') != 1:
        raise RuntimeError("SSRP FWMARK 上游结构变化，停止适配")
    result = original.replace('FWMARK="0x53535250"',
                              '# Low 16 bits: do not overlap QModem uplink mark bits.\nFWMARK="0x5250"')
    pattern = r'(\bip rule (?:add|del) fwmark )(\$\{FWMARK\}|"\$FWMARK")'
    result, count = re.subn(pattern, lambda m: m[1] +
                            ('"${FWMARK}/0x0000ffff"' if m[2].startswith('"')
                             else '${FWMARK}/0x0000ffff'), result)
    if count != 5:
        raise RuntimeError("SSRP 策略路由数量变化，停止适配")
    return result


def modem_hook_patch(original):
    marker = '# AW1000 wired-first hot standby enrollment'
    if marker in original:
        return original
    anchor = 'init_type=$2\n'
    if original.count(anchor) != 1:
        raise RuntimeError("QModem 扫描回调结构变化，停止适配")
    block = '''
# AW1000 wired-first hot standby enrollment
if [ "$init_type" = post_init ] && [ -x /usr/sbin/aw1000-uplink-enroll ]; then
    /usr/sbin/aw1000-uplink-enroll "$config_section" ||
        logger -t aw1000-failover "Modem enrollment deferred; inspect QModem configuration"
fi
'''
    return original.replace(anchor, anchor + block)


def modem_raw_ip_patch(original):
    marker = '# AW1000 managed QMI host Raw-IP preparation'
    if marker in original:
        return original
    anchor = '    while true; do\n        m_debug "dialing: $cmd_line"'
    if original.count(anchor) != 1:
        raise RuntimeError("QModem QMI dial loop changed; cannot install Raw-IP preparation")
    block = '''    while true; do
        # AW1000 managed QMI host Raw-IP preparation
        if [ "$managed_ipv4" = 1 ] && [ -x /usr/sbin/aw1000-qmi-prepare ]; then
            if ! /usr/sbin/aw1000-qmi-prepare "$modem_config" "${qmi_if:-}"; then
                m_debug "Raw-IP preparation failed; delaying QMI dial"
                sleep 5
                continue
            fi
        fi
        m_debug "dialing: $cmd_line"'''
    return original.replace(anchor, block)


def cm_raw_ip_patch(original):
    marker = '/* QModem managed host Raw-IP alignment */'
    if marker in original:
        return original
    replacements = {
        'static USHORT WdaSetDataFormat(PQMUX_MSG pMUXMsg, void *arg) {\n    QMAP_SETTING *qmap_settings = (QMAP_SETTING *)arg;':
        '''/* QModem managed host Raw-IP alignment */
typedef struct {
    QMAP_SETTING *settings;
    unsigned link_protocol;
} QMODEM_WDA_CONTEXT;

static USHORT WdaSetDataFormat(PQMUX_MSG pMUXMsg, void *arg) {
    QMODEM_WDA_CONTEXT *context = (QMODEM_WDA_CONTEXT *)arg;
    QMAP_SETTING *qmap_settings = context->settings;''',
        'linkProto->Value = cpu_to_le32(0x01);     /* Set Ethernet  mode */':
        'linkProto->Value = cpu_to_le32(context->link_protocol); /* Match managed host format */',
        '    QMAP_SETTING qmap_settings = {0};':
        '''    QMAP_SETTING qmap_settings = {0};
    QMODEM_WDA_CONTEXT wda_context = { &qmap_settings, 1 };
    if (profile->managed_ipv4) {
        char raw_path[256], raw_mode = 0;
        FILE *raw_file;
        snprintf(raw_path, sizeof(raw_path), "/sys/class/net/%s/qmi/raw_ip", profile->usbnet_adapter);
        raw_file = fopen(raw_path, "r");
        if (raw_file) {
            if (fread(&raw_mode, 1, 1, raw_file) == 1 && (raw_mode == 'Y' || raw_mode == '1'))
                wda_context.link_protocol = 2;
            fclose(raw_file);
        }
    }''',
        'WdaSetDataFormat, (void *)&qmap_settings);':
        'WdaSetDataFormat, (void *)&wda_context);'
    }
    for anchor, replacement in replacements.items():
        if original.count(anchor) != 1:
            raise RuntimeError("Quectel CM WDA layout changed; cannot align managed Raw-IP")
        original = original.replace(anchor, replacement)
    return original


def modem_ipv6_patch(original):
    marker = '# AW1000 netifd IPv6 defaults; IPv4 failover remains independent'
    if marker in original:
        return original
    anchor = '        if [ "$ra_master" = "1" ];then'
    if original.count(anchor) != 1:
        raise RuntimeError("QModem IPv6 setup layout changed")
    block = '''        # AW1000 netifd IPv6 defaults; IPv4 failover remains independent
        if [ "$managed_ipv4" = 1 ] && [ "$protov6" = dhcpv6 ]; then
            for pair in reqaddress:try reqprefix:auto delegate:1 "metric:$metric"; do
                local key=${pair%%:*}
                if [ -z "$(uci -q get "network.${interface6_name}.$key")" ]; then
                    uci set "network.${interface6_name}.$key=${pair#*:}"
                    network_reload_flag=1
                fi
            done
        fi
'''
    result = original.replace(anchor, block + anchor)
    anchor = '            uci set network.${interface6_name}.extendprefix=1'
    if result.count(anchor) != 1:
        raise RuntimeError("QModem IPv6 prefix extension layout changed")
    return result.replace(anchor, '''            [ "$(uci -q get network.${interface6_name}.extendprefix)" = 1 ] || network_reload_flag=1
''' + anchor)


def cm_ipv6_netifd_patch(original):
    marker = '/* Managed IPv6 belongs to netifd/odhcp6c, not direct CM commands. */'
    if marker in original:
        return original
    anchor = 'set_ipv6:\n    if (profile->ipv6.Address[0] && profile->ipv6.PrefixLengthIPAddr) {'
    if original.count(anchor) != 1:
        raise RuntimeError("Quectel CM IPv6 setup layout changed")
    return original.replace(anchor, '''set_ipv6:
    /* Managed IPv6 belongs to netifd/odhcp6c, not direct CM commands. */
    if (profile->managed_ipv4)
        return;
    if (profile->ipv6.Address[0] && profile->ipv6.PrefixLengthIPAddr) {''')


def modem_status_patch(original):
    marker = '# AW1000 managed QMI status comes from its matching netifd interface'
    if marker in original:
        return original
    anchor = '    connect_status="No"\n    driver=$(get_driver)'
    if original.count(anchor) != 1:
        raise RuntimeError('QModem connection status layout changed')
    block = '''    connect_status="No"
    # AW1000 managed QMI status comes from its matching netifd interface
    # QMI WDS sessions need not be listed by AT+CGACT. This reports a data
    # session, not an Internet health verdict; mwan3 remains the health owner.
    if [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = arcadyan,aw1000 ] &&
       [ "$(uci -q get "qmodem.${config_section}.host_ipv4_owner")" = netifd ]; then
        local managed_alias managed_status
        managed_alias=$(uci -q get "qmodem.${config_section}.alias")
        case "$managed_alias" in
            ''|*[!A-Za-z0-9_]*) managed_status='{}';;
            *) managed_status=$(ubus call "network.interface.$managed_alias" status 2>/dev/null) || managed_status='{}';;
        esac
        if printf '%s' "$managed_status" | jq -e --arg alias "$managed_alias" '
          .up==true and .proto=="qmodem_runtime" and .l3_device==$alias and
          (.["ipv4-address"] | length)>0 and
          any(.route[]?; .mask==0 and .target=="0.0.0.0")' >/dev/null 2>&1; then
            connect_status="Yes"
        fi
        add_plain_info_entry "connect_status" "$connect_status" "Connect Status"
        return
    fi
    driver=$(get_driver)'''
    return original.replace(anchor, block)


def modem_interface_patch(original):
    marker = '# AW1000 logical interface and kernel device are distinct'
    if marker in original:
        return original
    anchor = '        managed_alias=$(uci -q get "qmodem.${config_section}.alias")\n'
    if original.count(anchor) != 1:
        raise RuntimeError('QModem managed interface layout changed')
    result = original.replace(anchor, anchor + '''        # AW1000 logical interface and kernel device are distinct
        case "$managed_alias" in ''|-) managed_alias=$config_section;; esac
        local managed_device managed_owner
        managed_device=$(uci -q get "network.$managed_alias.device")
        [ -n "$managed_device" ] || managed_device=$managed_alias
        managed_owner=$(uci -q get "network.$managed_alias.modem_config")
''')
    anchor = '        if printf \'%s\' "$managed_status" | jq -e --arg alias "$managed_alias" \'\n'
    if result.count(anchor) != 1:
        raise RuntimeError('QModem managed device check layout changed')
    result = result.replace(anchor, '''        if [ -n "$managed_owner" ] && [ "$managed_owner" != "$config_section" ]; then managed_status='{}'; fi
        if printf '%s' "$managed_status" | jq -e --arg device "$managed_device" '
''')
    anchor = '.up==true and .proto=="qmodem_runtime" and .l3_device==$alias and'
    if result.count(anchor) != 1:
        raise RuntimeError('QModem L3 identity check layout changed')
    return result.replace(anchor, '.up==true and .proto=="qmodem_runtime" and .l3_device==$device and')


def modem_led_patch(original):
    marker = '# AW1000 steady network LED, no legacy ipq LED names'
    if marker in original:
        return original
    anchor = '    get_led "$modem_slot"\n    case $type in'
    if original.count(anchor) != 1:
        raise RuntimeError('QModem LED setup layout changed')
    return original.replace(anchor, '''    get_led "$modem_slot"
    # AW1000 steady network LED, no legacy ipq LED names
    if [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = arcadyan,aw1000 ]; then
        net_led=green:5g
        sim_led=''
    fi
    case $type in''').replace('set system.n${cfg_name}.mode="link tx rx"',
        'set system.n${cfg_name}.mode="link"')


def board_led_patch(original):
    anchor = "net_led 'ipq::led9' sim_led 'ipq::led2'"
    if "# AW1000 steady LED mapping" in original:
        return original
    if original.count(anchor) != 2:
        raise RuntimeError('AW1000 QModem board LED layout changed')
    return '# AW1000 steady LED mapping\n' + original.replace(anchor, "net_led 'green:5g' sim_led ''")


def led_service_patch(original):
    marker = '# AW1000 system netdev LEDs have one steady-state owner'
    if marker in original:
        return original
    anchor = 'start_service()\n{\n'
    if original.count(anchor) != 1:
        raise RuntimeError('QModem LED service layout changed')
    return original.replace(anchor, anchor + '''    # AW1000 system netdev LEDs have one steady-state owner
    [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" != arcadyan,aw1000 ] || return 0
''')


def modem_sim_led_patch(original):
    marker = '# AW1000 SIM readiness snapshot for the session LED'
    if marker in original:
        return original
    anchor = '    sim_fullfill=0\n'
    result = original.replace(anchor, anchor + '''    # AW1000 SIM readiness snapshot for the session LED
    if [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = arcadyan,aw1000 ]; then
        echo 0 > "$MODEM_RUNDIR/${modem_config}_dir/aw1000-sim-ready"
    fi
''')
    if original.count(anchor) != 1:
        raise RuntimeError('QModem SIM preparation layout changed')
    anchor = '    if [ "$sim_fullfill" = "1" ];then'
    if result.count(anchor) != 1:
        raise RuntimeError('QModem SIM LED layout changed')
    result = result.replace(anchor, '''    if [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = arcadyan,aw1000 ]; then
        printf '%s\\n' "$sim_fullfill" > "$MODEM_RUNDIR/${modem_config}_dir/aw1000-sim-ready"
    fi
''' + anchor)
    anchor = '        net_led=green:5g\n        sim_led=\'\'\n'
    if result.count(anchor) != 1:
        raise RuntimeError('AW1000 LED ownership adapter missing')
    return result.replace(anchor, anchor + '''        # Link/carrier on a USB netcard does not prove a SIM/data session.
        if [ "$type" = net ]; then
            [ ! -x /usr/sbin/aw1000-leds ] || /usr/sbin/aw1000-leds >/dev/null 2>&1
            return
        fi
''')


def modem_sim_state_patch(original):
    marker = '# AW1000 tri-state SIM publication'
    if marker in original:
        return original
    anchor = '    cpin=$(cmd_dial_cpin_query "$at_port")\n'
    if original.count(anchor) != 1:
        raise RuntimeError('QModem CPIN query layout changed')
    return original.replace(anchor, anchor + '''    # AW1000 tri-state SIM publication
    if [ "$(cat /tmp/sysinfo/board_name 2>/dev/null)" = arcadyan,aw1000 ] && [ -x /usr/sbin/aw1000-internet ]; then
        printf '%s\\n' "$cpin" | /usr/sbin/aw1000-internet --sim-result "$modem_config"
    fi
''')


def modem_procd_patch(original):
    """Keep the rc.common service builder separate from per-instance actions."""
    marker = '# AW1000 procd transaction isolation'
    if marker in original:
        return original
    replacements = {
        '    update_modem\n}\n\nstop_service':
        '    config_load qmodem\n    config_get g_enabled main enable_dial\n    config_foreach _declare_modem modem-device\n}\n\nstop_service',
        '\trc_procd _dial $1': '\trc_procd _dial "$1"',
        '    data=$(_procd_ubus_call list | jq -r  ".$service.instances.modem_$1")\n    [ "$data" == "null" ] && current_status=0 || current_status=1':
        '    local current_status=0\n    _instance_running "$1" && current_status=1',
        '        [ "$target" -eq 1 ] && dial $1 || hang $1':
        '        if [ "$target" -eq 1 ]; then dial "$1"; else hang "$1"; fi',
        '    data=$(_procd_ubus_call list | jq  -r ".$service.instances.$instances")':
        '    data=$(_instance_data "$1")',
    }
    for anchor, replacement in replacements.items():
        if original.count(anchor) != 1:
            raise RuntimeError('QModem procd startup structure changed; refusing unsafe patch')
        original = original.replace(anchor, replacement)
    anchor = '\n_dial()\n'
    if original.count(anchor) != 1:
        raise RuntimeError('QModem procd instance builder changed')
    return original.replace(anchor, '''
# AW1000 procd transaction isolation
# Never call rc_procd or JSON-mutating procd queries inside start_service.
_instance_data()
{
    ubus call service list '{"name":"qmodem_network"}' |
        jq -r --arg key "modem_$1" '.qmodem_network.instances[$key] // null'
}

_instance_running()
{
    _instance_data "$1" | jq -e '.running == true' >/dev/null 2>&1
}

_declare_modem()
{
    local enabled state
    config_get enabled "$1" enable_dial
    config_get state "$1" state
    if [ "$state" != disabled ] && [ "$enabled" != 0 ] && [ "$g_enabled" != 0 ]; then
        _dial "$1"
    elif _instance_running "$1"; then
        # _hang uses procd_kill, which clears JSON; isolate it from the builder.
        ( _hang "$1" )
    fi
}

_dial()
''')


def apply(tree):
    files = {
        'ssrp_mark': ('feeds/helloworld/luci-app-ssr-plus/root/usr/bin/ssr-rules', ssr_mark_patch),
        'qmodem_scan_hook': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/modem_hook.sh', modem_hook_patch),
        'qmodem_raw_ip': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/modem_dial.sh', modem_raw_ip_patch),
        'quectel_cm_raw_ip': ('feeds/qmodem/application/quectel_CM_5G_M/src/QMIThread.c', cm_raw_ip_patch),
        'qmodem_ipv6': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/modem_dial.sh', modem_ipv6_patch),
        'quectel_cm_ipv6': ('feeds/qmodem/application/quectel_CM_5G_M/src/udhcpc.c', cm_ipv6_netifd_patch),
        'qmodem_status': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/generic.sh', modem_status_patch),
        'qmodem_interface': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/generic.sh', modem_interface_patch),
        'qmodem_steady_led': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/modem_dial.sh', modem_led_patch),
        'qmodem_board_led': ('feeds/qmodem/application/qmodem/files/etc/board.d/03_qmodem', board_led_patch),
        'qmodem_led_service': ('feeds/qmodem/application/qmodem/files/etc/init.d/qmodem_led', led_service_patch),
        'qmodem_sim_state': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/modem_dial.sh', modem_sim_state_patch),
        'qmodem_sim_led': ('feeds/qmodem/application/qmodem/files/usr/share/qmodem/modem_dial.sh', modem_sim_led_patch),
        'qmodem_procd': ('feeds/qmodem/application/qmodem/files/etc/init.d/qmodem_network', modem_procd_patch)
    }
    report = {}
    for name, (relative, transform) in files.items():
        path = Path(tree) / relative
        patched = transform(path.read_text())
        path.write_text(patched)
        report[name] = {'path': relative, 'sha256': hashlib.sha256(patched.encode()).hexdigest()}
    # Several guarded transforms can touch one file; record its final bytes.
    for item in report.values():
        item['sha256'] = hashlib.sha256((Path(tree) / item['path']).read_bytes()).hexdigest()
    return report
