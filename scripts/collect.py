#!/usr/bin/env python3
"""Reject incomplete output; publish only images and credential-free records."""
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import features
import board_state
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


REQUIRED_PACKAGES = {
    "xupnpd", "luci-app-xupnpd", "liblua", "libopenssl", "rpcd-mod-file",
    "coremark", "mosdns", "luci-app-openclash", "ruby", "ruby-yaml", "bash",
    "curl", "unzip", "kmod-inet-diag", "kmod-nft-tproxy", "kmod-tun", "wifi-scripts", "kmod-gpio-button-hotplug",
    "luci", "luci-ssl", "luci-base", "luci-mod-system", "luci-compat", "luci-theme-argon",
    "luci-app-qmodem-next", "qmodem", "qmodem-settings", "qmodem-seal", "qmodem-smsd",
    "sms-forwarder-next", "modem_scan", "tom_modem", "ubus-at-daemon", "sms-tool_q",
    "quectel-CM-5G-M", "kmod-usb-net-qmi-wwan", "kmod-usb-serial-option",
    "luci-app-ssr-plus", "xray-core", "mihomo", "shadowsocksr-libev-ssr-redir",
    "dnsmasq-full", "luci-app-arpbind", "luci-app-samba4", "samba4-server",
    "luci-app-vsftpd", "vsftpd", "kmod-wireguard", "wireguard-tools", "luci-proto-wireguard",
    "luci-app-filetransfer", "luci-app-vlmcsd", "vlmcsd", "luci-app-upnp",
    "luci-app-cpufreq", "cpufreq", "luci-app-autoreboot", "luci-app-zerotier", "zerotier",
    "block-mount", "jq-full", "oniguruma", "flock", "bc", "coreutils-stat", "xxd", "usbutils", "ndisc6",
    "kmod-usb2", "kmod-usb3", "kmod-usb-serial", "kmod-usb-serial-qualcomm",
    "kmod-usb-net", "kmod-usb-acm", "kmod-usb-wdm", "kmod-usb-net-cdc-ether",
    "kmod-usb-net-cdc-mbim", "kmod-usb-net-rndis", "kmod-usb-net-cdc-ncm",
    "kmod-usb-net-huawei-cdc-ncm", "terminfo", "libqmodem-sms",
    "ipq-wifi-arcadyan_aw1000", "kmod-ath11k-ahb", "ath11k-firmware-ipq8074", "wpad-openssl",
    "mwan3", "luci-app-mwan3", "luci-app-qmodem-monitor", "qmodem_monitor",
    "ip-full", "conntrack", "nftables-json", "kmod-nft-nat", "kmod-usb-printer", "p910nd", "luci-app-p910nd",
    "kmod-aw1000-fxs", "fxsd", "luci-app-fxs",
    "odhcp6c", "odhcpd-ipv6only", "luci-proto-ipv6", "ppp", "ppp-mod-pppoe", "luci-proto-ppp"
}

RUNTIME_FILES = ["usr/sbin/aw1000-proxy-guard", "usr/bin/xupnpd", "etc/init.d/xupnpd",
                 "www/luci-static/resources/view/aw1000/xupnpd.js",
                 "usr/share/luci/menu.d/luci-app-xupnpd.json", "usr/share/rpcd/acl.d/luci-app-xupnpd.json", "etc/uci-defaults/zz-aw1000-features", "etc/rc.button/wlan", "etc/rc.button/wps", "etc/rc.wps/40-wps_ap",
                 "etc/init.d/aw1000-buttons", "usr/sbin/aw1000-coremark", "bin/coremark",
                 "usr/bin/mosdns", "etc/init.d/mosdns", "etc/mosdns/config.yaml",
                 "etc/init.d/openclash", "usr/libexec/mihomo","usr/sbin/aw1000-internet", "usr/libexec/rpcd/aw1000.internet",
                 "www/luci-static/resources/view/aw1000/internet.js",
                 "usr/share/luci/menu.d/aw1000-internet.json", "usr/share/rpcd/acl.d/aw1000-internet.json",
                 "lib/netifd/proto/ppp.sh",
                 "www/luci-static/resources/protocol/qmodem_runtime.js",
                 "lib/netifd/proto/qmodem_runtime.sh", "usr/sbin/fxsd",
                 "usr/sbin/aw1000-phone-setup", "usr/sbin/aw1000-phone-port",
                 "usr/sbin/aw1000-phone-at", "usr/sbin/aw1000-fxsd-run",
                 "www/luci-static/resources/view/fxs/phone.js",
                 "usr/share/luci/menu.d/luci-app-fxs.json", "usr/lib/lua/luci/i18n/fxs.zh-cn.lmo",
                 "usr/share/rpcd/acl.d/luci-app-fxs.json", "etc/fxs/si3226x_patch.bin",
                 "etc/init.d/fxsd", "usr/libexec/rpcd/fxs", "etc/init.d/p910nd", "usr/sbin/aw1000-qmi-prepare",
                 "lib/netifd/proto/dhcpv6.sh", "usr/sbin/odhcp6c", "usr/sbin/aw1000-ipv6-follow", "usr/sbin/aw1000-mwan-follow",
                 "usr/sbin/aw1000-leds", "usr/libexec/qmodem-uplink-health", "lib/mwan3/mwan3-skeleton.nft", "usr/sbin/mwan3track", "usr/sbin/mwan3ct"]

SSR_PACKAGES = {'luci-app-ssr-plus', 'xray-core', 'mihomo', 'shadowsocksr-libev-ssr-redir'}
OC_PACKAGES = {'luci-app-openclash', 'mihomo', 'ruby', 'ruby-yaml', 'bash',
               'curl', 'unzip', 'kmod-inet-diag', 'kmod-nft-tproxy', 'kmod-tun'}


def selected_requirements(options):
    packages = REQUIRED_PACKAGES - SSR_PACKAGES - OC_PACKAGES
    packages = packages | {'luci-i18n-aw1000-internet-zh-cn'}
    if options['include_ssr_plus']:
        packages |= SSR_PACKAGES
    if options['include_openclash']:
        packages |= OC_PACKAGES
    runtime = list(RUNTIME_FILES)
    if not options['include_openclash']:
        runtime.remove('etc/init.d/openclash')
    if not any(options[key] for key in features.PROXY_DEFAULTS):
        runtime.remove('usr/libexec/mihomo')
    runtime.append('usr/lib/lua/luci/i18n/aw1000-internet.zh-cn.lmo')
    if board_state.enabled(options):
        packages |= {board_state.PACKAGE, 'coreutils-timeout'}
        runtime += board_state.RUNTIME_FILES
    return packages, runtime


def manifest_identities(actual, metadata):
    """Map only exact ABI package names; never expand virtual Provides."""
    packages = set(actual)
    aliases = {}
    name = None
    for line in Path(metadata).read_text().splitlines():
        if line.startswith('Package: '):
            name = line[9:].strip()
        elif line.startswith('ABI-Version: ') and name:
            abi = line[13:].strip()
            if not abi or name.startswith('kmod-'):
                continue
            # Same rule as include/feeds.mk FormatABISuffix.
            binary = name + ('-' if name[-1:].isdigit() else '') + abi
            if binary in actual:
                packages.add(name)
                aliases[name] = binary
    return packages, aliases


def build_date():
    return datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d')


def main():
    tree = ROOT / "work/immortalwrt"
    target = tree / "bin/targets/qualcommax/ipq807x"
    manifests = list(target.glob("*arcadyan_aw1000*.manifest"))
    if not manifests:
        # A single-profile source build can emit a target-wide manifest.
        manifests = list(target.glob("*.manifest"))
    if len(manifests) != 1:
        raise RuntimeError("未找到唯一的 AW1000 软件包 manifest")
    actual = {line.split()[0] for line in manifests[0].read_text().splitlines() if line.strip()}
    selected = set(json.loads((ROOT / "work/selected-packages.json").read_text()))
    # Build-only/virtual packages can have metadata but no manifest record. The
    # explicit feature roots and the complete QModem runtime closure are checked.
    options = features.build_options(ROOT)
    required, runtime_files = selected_requirements(options)
    identities, abi_aliases = manifest_identities(actual, tree / "tmp/.packageinfo")
    missing = sorted(required - identities)
    if missing:
        raise RuntimeError("以下组件未实际进入镜像，拒绝交付：\n" + "\n".join(missing))
    if any(name in actual for name in ("luci-app-argon-config", "luci-app-qmodem", "jq", "luci-app-aw1k-led", "qmodem-uplink-manager", "luci-app-qmodem-uplink")):
        raise RuntimeError("镜像包含明确排除或会冲突的组件")
    for key, package in [('include_ssr_plus', 'luci-app-ssr-plus'), ('include_openclash', 'luci-app-openclash')]:
        if not options[key] and package in actual:
            raise RuntimeError('镜像包含未选中的代理组件：' + package)
    roots = list(tree.glob("build_dir/target-*/root-qualcommax"))
    if len(roots) != 1:
        raise RuntimeError('未找到唯一的目标根文件系统')
    absent = [path for path in runtime_files if not (roots[0] / path).is_file()]
    if absent:
        raise RuntimeError('目标根文件系统缺少以下运行文件：\n' + '\n'.join(absent))
    if not any(path.is_file() for path in (roots[0] / "usr/lib").glob("libonig.so.*")):
        raise RuntimeError("镜像缺少 jq-full 所需的 libonig 共享库")
    board_state.validate_root(roots[0], board_state.enabled(options))
    if not board_state.enabled(options) and board_state.PACKAGE in actual:
        raise RuntimeError('镜像包含未选中的初始化组件')
    core = roots[0] / "etc/openclash/core/clash_meta"
    if options["include_openclash"] and (not core.is_symlink() or str(core.readlink()) != "/usr/libexec/mihomo"):
        raise RuntimeError("OpenClash 内置核心链接缺失或指向错误")
    if not options['include_openclash'] and core.is_symlink():
        raise RuntimeError('OpenClash 未选中但核心链接仍存在')
    import phone
    expected_patch = phone.check_patch()
    installed_patch = roots[0] / "etc/fxs/si3226x_patch.bin"
    if hashlib.sha256(installed_patch.read_bytes()).hexdigest() != expected_patch["sha256"]:
        raise RuntimeError("镜像中的 SLIC 补丁与已验证文件不一致")
    if not list((roots[0] / "lib/modules").glob("*/aw1000-fxs.ko*")):
        raise RuntimeError("镜像缺少匹配内核的 AW1000 FXS 驱动")
    images = [p for p in sorted(target.iterdir()) if "arcadyan_aw1000" in p.name
              and p.suffix in {".bin", ".ubi", ".itb"}]
    if not any("sysupgrade" in p.name for p in images):
        raise RuntimeError("没有生成 AW1000 sysupgrade 镜像")
    output = ROOT / "output"
    output.mkdir(exist_ok=True)
    if any(output.iterdir()):
        raise RuntimeError("输出目录非空，拒绝混入其他编译结果")
    stamp = build_date()
    exported = []
    for image in images:
        filename = image.stem + '-' + stamp + image.suffix
        shutil.copy2(image, output / filename)
        exported.append(filename)
    shutil.copy2(manifests[0], output / "firmware.manifest")
    shutil.copy2(ROOT / "work/sources-resolved.json", output / "sources-resolved.json")
    shutil.copy2(ROOT / "work/selected-packages.json", output / "selected-packages.json")
    shutil.copy2(ROOT / "work/xray-go-compatibility.json", output / "xray-go-compatibility.json")
    shutil.copy2(ROOT / "work/build-features.json", output / "build-features.json")
    shutil.copy2(tree / ".config", output / "build.config")
    diff = subprocess.run(["./scripts/diffconfig.sh"], cwd=tree, check=True,
                          text=True, capture_output=True).stdout
    (output / "diffconfig").write_text(diff)
    report = {"requested_features_verified": True, "build_features": options, "manifest_packages": len(actual),
              "selected_build_packages": len(selected), "required_packages_verified": sorted(required),
              "abi_package_aliases": abi_aliases,
              "runtime_files_verified": runtime_files,
              "build_date": stamp, "build_timezone": "Asia/Shanghai",
              "source_images": [p.name for p in images], "images": exported}
    (output / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    checksums = []
    for path in sorted(output.iterdir()):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksums.append(f"{digest}  {path.name}")
    (output / "sha256sums").write_text("\n".join(checksums) + "\n")
    print(f"通过：{len(required)} 个必需组件实际进入镜像；输出 {len(images)} 个 AW1000 镜像。")



def build_summary(root):
    settings = json.loads((root / '.private/settings.json').read_text())
    lock = json.loads((root / 'sources.lock.json').read_text())
    source = json.loads((root / 'output/sources-resolved.json').read_text())
    raw_options = json.loads((root / 'output/build-features.json').read_text())
    options = {**features.proxy_options(raw_options), 'include_device_init': board_state.enabled(raw_options)}
    verification = json.loads((root / 'output/verification.json').read_text())
    def safe(value):
        return str(value).replace('\n', ' ').replace('\r', ' ').replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;')
    rows = [
        ('工程版本', 'v' + lock['build_project_version']),
        ('固件系统', 'ImmortalWrt ' + source.get('immortalwrt_ref', '见来源记录')),
        ('固件日期', verification.get('build_date', '见文件名') + '（北京时间）'),
        ('设备', 'Arcadyan AW1000 / IPQ8072A / RG500Q-EA'),
        ('默认管理 IP', settings['lan_ip']),
        ('管理地址', 'http://' + settings['lan_ip']),
        ('管理账号', 'root'),
        ('管理密码', '首次初始化按IMEI派生（初始化前密码登录锁定）' if options['include_device_init'] else os.environ.get('BUILD_PASSWORD_SOURCE', '本次构建提供的密码') + '（不显示明文）'),
        ('包含初始化组件', '是（成功后停用入口；只读固件仍含代码）' if options['include_device_init'] else '否（使用指定管理密码）'),
        ('WiFi国家码', 'CN（固定）'),
        ('Vlmcsd KMS', '默认启用，允许客户端自动发现；不允许互联网连接'),
        ('默认上网方式', '仅有线网络'),
        ('包含 SSR Plus+', '是' if options['include_ssr_plus'] else '否'),
        ('包含 OpenClash', '是' if options['include_openclash'] else '否'),
        ('镜像组件校验', '通过' if verification.get('requested_features_verified') else '未通过'),
    ]
    text = '## AW1000 固件构建摘要\n\n| 项目 | 内容 |\n| --- | --- |\n'
    text += ''.join('| ' + safe(key) + ' | ' + safe(value) + ' |\n' for key, value in rows)
    text += '\n固件文件：\n\n' + ''.join('- `' + safe(name).replace('`', '') + '`\n' for name in verification['images'])
    text += '\n请在本次运行的 Artifacts 下载固件、校验文件和构建记录。默认 IP 和密码用于首次刷入或恢复出厂；保留配置升级时沿用原设置。\n'
    return text


def write_summary():
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if not path:
        raise RuntimeError('未设置 GITHUB_STEP_SUMMARY')
    with open(path, 'a') as stream:
        stream.write(build_summary(ROOT))


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--summary"]:
        write_summary()
    else:
        main()
