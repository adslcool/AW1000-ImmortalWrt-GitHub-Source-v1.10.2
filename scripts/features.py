"""Generate Wi-Fi buttons, one-shot benchmark and optional service integration."""
import json
from pathlib import Path

PROXY_DEFAULTS = {'include_ssr_plus': True, 'include_openclash': True}


def proxy_options(settings=None):
    settings = settings or {}
    result = {key: settings.get(key, default) for key, default in PROXY_DEFAULTS.items()}
    if any(type(value) is not bool for value in result.values()):
        raise ValueError('Proxy build options must be booleans')
    return result


def effective_config(text, options):
    options = proxy_options(options)
    lines = []
    oc_explicit = {'bash', 'curl', 'ruby', 'ruby-yaml', 'kmod-inet-diag',
                   'kmod-nft-tproxy', 'kmod-tun', 'unzip'}
    for line in text.splitlines():
        if not options['include_ssr_plus'] and line.removeprefix('# ').startswith('CONFIG_PACKAGE_luci-app-ssr-plus_'):
            continue  # Hidden Kconfig choices may retain defaults with the app disabled.
        if not options['include_ssr_plus'] and line.startswith('CONFIG_PACKAGE_luci-app-ssr-plus'):
            line = '# ' + line.split('=')[0] + ' is not set'
        if not options['include_openclash']:
            if line == 'CONFIG_PACKAGE_luci-app-openclash=y':
                line = '# CONFIG_PACKAGE_luci-app-openclash is not set'
            if line in {'CONFIG_PACKAGE_' + name + '=y' for name in oc_explicit}:
                continue  # Other selected packages may still need these libraries.
        if not any(options.values()) and line == 'CONFIG_PACKAGE_mihomo=y':
            line = '# CONFIG_PACKAGE_mihomo is not set'
        lines.append(line)
    if not options['include_ssr_plus']:
        lines += ['# CONFIG_PACKAGE_xray-core is not set',
                  '# CONFIG_PACKAGE_shadowsocksr-libev-ssr-redir is not set']
    return '\n'.join(lines) + '\n'


def build_options(root):
    path = root / 'work/build-features.json'
    raw = json.loads(path.read_text())
    import board_state
    return {**proxy_options(raw), 'include_device_init': board_state.enabled(raw)}


BUTTON = r'''#!/bin/sh
[ "${ACTION:-}" = released ] || exit 0
mkdir -p /var/run
exec 9>/var/run/aw1000-wifi.lock
flock -n 9 || exit 0
if [ -f /var/run/aw1000-wifi-off ]; then
    /sbin/wifi up || exit 1
    rm -f /var/run/aw1000-wifi-off
else
    /sbin/wifi down || exit 1
    touch /var/run/aw1000-wifi-off
fi
/usr/sbin/aw1000-leds >/dev/null 2>&1 || true
'''

BOOT = r'''#!/bin/sh /etc/rc.common
# config_foreach invokes the radio and AP callbacks indirectly.
# shellcheck shell=ash disable=SC2034,SC2317,SC2329
START=99
start() {
    . /lib/functions.sh
    enable_radio() { uci set "wireless.$1.disabled=0"; }
    enable_wps() {
        local mode encryption
        config_get mode "$1" mode
        config_get encryption "$1" encryption
        [ "$mode" = ap ] || return 0
        case "$encryption" in
            psk2|psk2+*) uci set "wireless.$1.wps_pushbutton=1";;
        esac
    }
    config_load wireless
    config_foreach enable_radio wifi-device
    config_foreach enable_wps wifi-iface
    uci commit wireless
    rm -f /var/run/aw1000-wifi-off
    /sbin/wifi up
}
'''

COREMARK = r'''#!/bin/sh
# One successful benchmark, then remove only its own cron entry.
exec 9>/var/run/aw1000-coremark.lock
flock -n 9 || exit 0
log=$(mktemp /tmp/coremark.XXXXXX) || exit 1
trap 'rm -f "$log"' EXIT HUP INT TERM
if nice -n 19 /bin/coremark >"$log" 2>&1; then
    mv -f "$log" /tmp/coremark.log
    sed -i '\|^[[:space:]]*0[[:space:]]5[[:space:]]\*[[:space:]]\*[[:space:]]\*[[:space:]]/usr/sbin/aw1000-coremark[[:space:]]*$|d' /etc/crontabs/root || exit 1
    crontab /etc/crontabs/root || exit 1
    logger -t aw1000-coremark 'One-shot benchmark completed; schedule removed: /tmp/coremark.log' 
else
    logger -t aw1000-coremark 'Benchmark failed; schedule retained for retry'
    exit 1
fi
'''

DEFAULTS = r'''#!/bin/sh
mkdir -p /etc/crontabs
[ -f /etc/crontabs/root ] || touch /etc/crontabs/root
sed -i '\|/etc/coremark.sh|d;\|/usr/sbin/aw1000-coremark|d' /etc/crontabs/root
chmod 600 /etc/crontabs/root
printf '%s\n' '0 5 * * * /usr/sbin/aw1000-coremark' >> /etc/crontabs/root
/etc/init.d/cron enable
/etc/init.d/aw1000-buttons enable
/etc/init.d/xupnpd stop
/etc/init.d/xupnpd disable
/etc/init.d/vlmcsd enable
# Defaults are disabled until a subscription / DNS integration is configured.
if [ -f /etc/config/openclash ] && ! uci -q get openclash.config.enable >/dev/null; then
    uci -q set openclash.config.enable=0
    uci -q commit openclash
fi
exit 0
'''

MOSDNS_INIT = r'''#!/bin/sh /etc/rc.common
# shellcheck shell=ash disable=SC2034,SC2329
USE_PROCD=1
START=60
STOP=20
start_service() {
    local enabled
    enabled=$(uci -q get mosdns.main.enabled)
    [ "$enabled" = 1 ] || return 0
    procd_open_instance
    procd_set_param command /usr/bin/mosdns start -c /etc/mosdns/config.yaml
    procd_set_param respawn
    procd_set_param file /etc/mosdns/config.yaml
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
service_triggers() { procd_add_reload_trigger mosdns; }
'''

MOSDNS_CONFIG = '''log:
  level: info
plugins:
  - tag: cache
    type: cache
    args:
      size: 4096
  - tag: upstream
    type: forward
    args:
      concurrent: 2
      upstreams:
        - addr: https://dns.alidns.com/dns-query
          dial_addr: 223.5.5.5
        - addr: https://doh.pub/dns-query
          dial_addr: 119.29.29.29
  - tag: main
    type: sequence
    args:
      - exec: $cache
      - matches: has_resp
        exec: accept
      - exec: $upstream
  - tag: udp
    type: udp_server
    args:
      entry: main
      listen: 127.0.0.1:6053
  - tag: tcp
    type: tcp_server
    args:
      entry: main
      listen: 127.0.0.1:6053
'''


def prepare(tree, proxy_patches=True):
    """Use our 05:00 scheduler and generate the matching xupnpd UI package."""
    install_xupnpd(tree)
    if proxy_patches:
        patch_proxy_services(tree)
    path = tree / 'feeds/packages/utils/coremark/Makefile'
    text = path.read_text()
    if '# AW1000 recurring scheduler' in text:
        return
    start = text.index('define Package/coremark/postinst\n')
    end = text.index('\nendef', start)
    text = text[:start] + 'define Package/coremark/postinst\n#!/bin/sh\n# AW1000 recurring scheduler\nexit 0' + text[end:]
    anchor = '\t$(INSTALL_BIN) ./coremark $(1)/etc/uci-defaults/xxx-coremark'
    if text.count(anchor) != 1:
        raise RuntimeError('CoreMark defaults changed upstream')
    path.write_text(text.replace(anchor, '\t# AW1000 recurring scheduler owns crontab setup'))


def install(tree, options=None):
    options = proxy_options(options)
    files = tree / 'files'
    core = files / 'etc/openclash/core/clash_meta'
    if not options['include_openclash'] and core.is_symlink():
        core.unlink()
    entries = {
        'etc/config/vlmcsd': "config vlmcsd 'config'\n\toption enabled '1'\n\toption auto_activate '1'\n\toption internet_access '0'\n",
        'usr/sbin/aw1000-proxy-guard': PROXY_GUARD,
        'etc/rc.button/wlan': BUTTON,
        'etc/init.d/aw1000-buttons': BOOT,
        'usr/sbin/aw1000-coremark': COREMARK,
        'etc/coremark.sh': '#!/bin/sh\nexec /usr/sbin/aw1000-coremark\n',
        'etc/uci-defaults/zz-aw1000-features': DEFAULTS,
        'etc/init.d/mosdns': MOSDNS_INIT,
        'etc/config/mosdns': "config mosdns 'main'\n\toption enabled '0'\n",
        'etc/mosdns/config.yaml': MOSDNS_CONFIG,
    }
    for name, content in entries.items():
        path = files / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o644 if name.startswith(('etc/config/', 'etc/mosdns/')) else 0o755)
    for name, target in {
        'etc/rc.d/S99aw1000-buttons': '../init.d/aw1000-buttons',
        'etc/rc.d/S99vlmcsd': '../init.d/vlmcsd',
        **({'etc/openclash/core/clash_meta': '/usr/libexec/mihomo'} if options['include_openclash'] else {}),
    }.items():
        path = files / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        path.symlink_to(target)


PROXY_GUARD = r"""#!/bin/sh
# A fail-closed owner record serializes proxy startup and teardown.
set -eu
RUN=${AW1000_PROXY_RUN:-/var/run/aw1000-proxy}
OC_INIT=${AW1000_OPENCLASH_INIT:-/etc/init.d/openclash}
action=${1:-}
name=${2:-}
case "$name" in shadowsocksr|openclash) :;; *) exit 2;; esac
mkdir -p "$RUN"
exec 9>"$RUN/lock"
flock -x 9
owner=$(cat "$RUN/owner" 2>/dev/null || true)
case "$action" in
    start)
        if [ "$name" = shadowsocksr ] && [ -x "$OC_INIT" ] && [ "$(uci -q get openclash.config.enable || true)" = 1 ]; then
            logger -t aw1000-proxy 'SSR Plus+ refused: disable and stop OpenClash first'
            exit 1
        fi
        if [ -n "$owner" ] && [ "$owner" != "$name" ]; then
            logger -t aw1000-proxy "Startup refused: stop $owner before starting $name"
            exit 1
        fi
        printf '%s\n' "$name" > "$RUN/owner"
        ;;
    stop)
        # An inactive service must not restore DNS over the active service.
        [ -z "$owner" ] || [ "$owner" = "$name" ] || exit 1
        printf '%s\n' "$name" > "$RUN/owner"
        ;;
    release)
        [ "$owner" != "$name" ] || rm -f "$RUN/owner"
        ;;
    *) exit 2;;
esac
"""


def patch_proxy_services(tree):
    changes = [
        ('feeds/helloworld/luci-app-ssr-plus/root/etc/init.d/shadowsocksr', [
            ('start() {\n', 'start() {\n\t/usr/sbin/aw1000-proxy-guard start shadowsocksr || return 1\n'),
            ('stop() {\n', 'stop() {\n\t/usr/sbin/aw1000-proxy-guard stop shadowsocksr || return 0\n'),
            ('\tunset_lock\n}\n\nreset()', '\tunset_lock\n\t/usr/sbin/aw1000-proxy-guard release shadowsocksr\n}\n\nreset()')]),
        ('feeds/luci/applications/luci-app-openclash/root/etc/init.d/openclash', [
            ('   if procd_running "openclash" >/dev/null; then', '   /usr/sbin/aw1000-proxy-guard start openclash || return 1\n   if procd_running "openclash" >/dev/null; then'),
            ('stop_service()\n{\n', 'stop_service()\n{\n   /usr/sbin/aw1000-proxy-guard stop openclash || return 0\n'),
            ('   echo "OpenClash Already Stop!"\n}', '   echo "OpenClash Already Stop!"\n   /usr/sbin/aw1000-proxy-guard release openclash\n}')])
    ]
    for name, pairs in changes:
        path = tree / name
        text = path.read_text()
        if '# AW1000 proxy lifecycle exclusion' in text:
            continue
        for old, new in pairs:
            if text.count(old) != 1:
                raise RuntimeError('Proxy lifecycle layout changed: ' + name)
            text = text.replace(old, new, 1)
        path.write_text(text + '\n# AW1000 proxy lifecycle exclusion\n')


XUPNPD_VIEW = r"""'use strict';
'require view';
'require fs';
'require rpc';
'require ui';
var serviceList = rpc.declare({object: 'service', method: 'list', params: ['name'], expect: {}});
return view.extend({
    load: function() {
        return Promise.all([
            fs.read('/usr/share/xupnpd/xupnpd.lua'),
            fs.read('/usr/share/xupnpd/playlists/aw1000.m3u').catch(function() { return '#EXTM3U\n'; }),
            serviceList('xupnpd'), fs.exec('/etc/init.d/xupnpd', ['enabled'])
        ]);
    },
    render: function(data) {
        var self = this;
        var instances = (data[2].xupnpd || {}).instances || {};
        var running = Object.keys(instances).some(function(k) { return instances[k].running; });
        this.cfg = E('textarea', {'class': 'cbi-input-textarea', rows: 20, style: 'width:100%;font-family:monospace'}, data[0]);
        this.playlist = E('textarea', {'class': 'cbi-input-textarea', rows: 10, style: 'width:100%;font-family:monospace'}, data[1]);
        function button(label, action) {
            return E('button', {'class': 'cbi-button cbi-button-action', click: ui.createHandlerFn(self, function() {
                return self.action(action);
            })}, label);
        }
        return E('div', {}, [
            E('h2', {}, 'xupnpd IPTV / DLNA'),
            E('p', {}, '服务状态：' + (running ? '运行中' : '已停止') + '；开机启动：' + (data[3].code === 0 ? '已启用' : '已停用')),
            E('div', {'class': 'cbi-section'}, [button('启动', 'start'), ' ', button('停止', 'stop'), ' ', button('重启', 'restart'), ' ', button('启用开机启动', 'enable'), ' ', button('停用开机启动', 'disable')]),
            E('p', {}, '仅在局域网提供 IPTV / DLNA 播放列表。配置中的 ssdp_interface 应为 br-lan；组播流还需要 udpxy 转换。'),
            E('h3', {}, '服务配置（Lua）'), this.cfg,
            E('h3', {}, '本机 M3U 播放列表（aw1000.m3u）'), this.playlist,
            E('p', {}, '保存后可点击“重启”加载新配置。其他播放列表文件保留。')
        ]);
    },
    action: function(action) {
        return fs.exec('/etc/init.d/xupnpd', [action]).then(function(result) {
            if (result.code !== 0) throw new Error(result.stderr || '操作失败');
            window.location.reload();
        }).catch(function(error) { ui.addNotification(null, E('p', {}, error.message), 'error'); });
    },
    handleSave: function() {
        return Promise.all([
            fs.write('/usr/share/xupnpd/xupnpd.lua', this.cfg.value),
            fs.write('/usr/share/xupnpd/playlists/aw1000.m3u', this.playlist.value)
        ]).then(function() { ui.addNotification(null, E('p', {}, '配置已保存，重启服务后生效。'), 'info'); });
    },
    handleSaveApply: null,
    handleReset: null
});
"""


def install_xupnpd(tree):
    package = tree / 'package/custom/luci-app-xupnpd'
    package.mkdir(parents=True, exist_ok=True)
    (package / 'Makefile').write_text('''include $(TOPDIR)/rules.mk
PKG_NAME:=luci-app-xupnpd
PKG_VERSION:=1.0.0
PKG_RELEASE:=1
PKG_LICENSE:=MIT
include $(INCLUDE_DIR)/package.mk

define Package/luci-app-xupnpd
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=AW1000 xupnpd administration
  DEPENDS:=+xupnpd +luci-base +rpcd-mod-file
  PKGARCH:=all
endef

define Build/Compile
endef

define Package/luci-app-xupnpd/install
	$(CP) ./root/* $(1)/
endef

$(eval $(call BuildPackage,luci-app-xupnpd))
''')
    root = package / 'root'
    entries = {
        'www/luci-static/resources/view/aw1000/xupnpd.js': XUPNPD_VIEW,
        'usr/share/luci/menu.d/luci-app-xupnpd.json': json.dumps({
            'admin/services/xupnpd': {'title': 'IPTV / DLNA', 'order': 70,
                'action': {'type': 'view', 'path': 'aw1000/xupnpd'},
                'depends': {'acl': ['luci-app-xupnpd']}}}),
        'usr/share/rpcd/acl.d/luci-app-xupnpd.json': json.dumps({
            'luci-app-xupnpd': {'description': 'Manage xupnpd configuration and service',
                'read': {'ubus': {'service': ['list']}, 'file': {
                    '/usr/share/xupnpd/xupnpd.lua': ['read'],
                    '/usr/share/xupnpd/playlists/aw1000.m3u': ['read'],
                    '/etc/init.d/xupnpd enabled': ['exec']}},
                'write': {'file': {'/usr/share/xupnpd/xupnpd.lua': ['write'],
                    '/usr/share/xupnpd/playlists/aw1000.m3u': ['write'],
                    **{'/etc/init.d/xupnpd ' + action: ['exec'] for action in ('start', 'stop', 'restart', 'enable', 'disable')}}}}})
    }
    for name, text in entries.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


if __name__ == '__main__':
    import sys
    install(Path(sys.argv[1]))
    install_xupnpd(Path(sys.argv[1]))
