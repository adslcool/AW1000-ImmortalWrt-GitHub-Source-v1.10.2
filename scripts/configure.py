#!/usr/bin/env python3
"""Render a private overlay, resolve dependency closure, reject dropped options."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import uuid
import internet
import features
import board_state

ROOT = Path(__file__).resolve().parents[1]


def overlay(tree, settings):
    internet.install(tree)
    features.install(tree, features.proxy_options(settings))
    board_state.install(tree, settings)
    files = tree / "files"
    mapping = {"lan_ip": "LAN_IP", "netmask": "NETMASK", "ssid_24g": "SSID_24G",
               "ssid_5g": "SSID_5G", "wifi_password": "WIFI_PASSWORD",
               "root_password_hash": "ROOT_PASSWORD_HASH", "country_code": "COUNTRY_CODE"}
    destination = files / "etc/aw1000-build.conf"
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("# Generated private firmware settings. Never commit this file.\n")
        for key, name in mapping.items():
            stream.write(name + "=" + shlex.quote(settings[key]) + "\n")
        stream.write('DEVICE_INIT_ENABLED=' + ('1' if board_state.enabled(settings) else '0') + '\n')
    os.chmod(destination, 0o600)
    for source, target in (("aw1000-provision.sh", "usr/sbin/aw1000-provision"),
                           ("99-aw1000", "etc/uci-defaults/99-aw1000"),
                           ("aw1000-firstboot.init", "etc/init.d/aw1000-firstboot"),
                           ("91-aw1000-failover", "etc/uci-defaults/91-aw1000-failover"),
                           ("92-aw1000-v160", "etc/uci-defaults/92-aw1000-v160"),
                           ("aw1000-leds.sh", "usr/sbin/aw1000-leds"),
                           ("aw1000-uplink-enroll.sh", "usr/sbin/aw1000-uplink-enroll"),
                           ("aw1000-qmi-prepare.sh", "usr/sbin/aw1000-qmi-prepare"),
                           ("aw1000-ipv6-follow.sh", "usr/sbin/aw1000-ipv6-follow"),
                           ("aw1000-mwan-follow.sh", "usr/sbin/aw1000-mwan-follow"),
                           ("aw1000-mwan-health.sh", "usr/libexec/qmodem-uplink-health"),
                           ("aw1000-mwan-follow.init", "etc/init.d/aw1000-mwan-follow"),
                           ("98-aw1000-mirrors", "etc/uci-defaults/98-aw1000-mirrors"),
                           ("aw1000-uplink-enroll.init", "etc/init.d/aw1000-uplink-enroll")):
        path = files / target
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "files" / source, path)
        path.chmod(0o755)
    protocol = files / "www/luci-static/resources/protocol/qmodem_runtime.js"
    protocol.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "files/qmodem_runtime.js", protocol)
    protocol.chmod(0o644)
    rc = files / "etc/rc.d/S99aw1000-firstboot"
    rc.parent.mkdir(parents=True, exist_ok=True)
    if rc.is_symlink():
        rc.unlink()
    rc.symlink_to("../init.d/aw1000-firstboot")
    rc_enroll = files / "etc/rc.d/S99aw1000-uplink-enroll"
    if rc_enroll.is_symlink():
        rc_enroll.unlink()
    rc_enroll.symlink_to("../init.d/aw1000-uplink-enroll")
    (files / "etc/config").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "files/mwan3.config", files / "etc/config/mwan3")
    template = files / "usr/share/aw1000/mwan3.default"
    template.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "files/mwan3.config", template)
    rc_follow = files / "etc/rc.d/S99aw1000-mwan-follow"
    if rc_follow.is_symlink():
        rc_follow.unlink()
    rc_follow.symlink_to("../init.d/aw1000-mwan-follow")
    rc_mwan = files / "etc/rc.d/S20mwan3"
    if rc_mwan.is_symlink():
        rc_mwan.unlink()
    rc_mwan.symlink_to("../init.d/mwan3")


def parse_config(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = "n"
    return values


def check_config(tree):
    requested_path = ROOT / 'work/requested.config'
    requested = parse_config(requested_path if requested_path.exists() else ROOT / 'config/aw1000.config')
    actual = parse_config(tree / ".config")
    failures = [key for key, value in requested.items()
                if (value == "y" and actual.get(key) != "y") or
                (value == "n" and actual.get(key, "n") != "n")]
    if failures:
        raise RuntimeError("以下配置被依赖解析删除或发生冲突，停止构建：\n" + "\n".join(failures))
    metadata = tree / "tmp/.packageinfo"
    if not metadata.is_file():
        raise RuntimeError("缺少软件包元数据，不能核对固件清单")
    packages = {line[9:] for line in metadata.read_text().splitlines() if line.startswith("Package: ")}
    selected = sorted(key.removeprefix("CONFIG_PACKAGE_") for key, value in actual.items()
                      if key.startswith("CONFIG_PACKAGE_") and value == "y" and
                      key.removeprefix("CONFIG_PACKAGE_") in packages)
    (ROOT / "work/selected-packages.json").write_text(json.dumps(selected, indent=2) + "\n")
    print(f"全部指定配置保留；已解析 {len(selected)} 个选中软件包（包括自动依赖）。")


def main():
    tree = ROOT / "work/immortalwrt"
    if not (ROOT / "work/sources-resolved.json").is_file():
        raise RuntimeError("请先完成 scripts/prepare.py，再生成配置")
    backend = tree / "feeds/qmodem/application/qmodem/files/lib/netifd/proto/qmodem_runtime.sh"
    if not backend.is_file() or "add_protocol qmodem_runtime" not in backend.read_text():
        raise RuntimeError("QModem runtime 后端缺失，不能仅安装 LuCI 协议界面")
    settings = json.loads((ROOT / ".private/settings.json").read_text())
    overlay(tree, settings)
    options = {**features.proxy_options(settings), 'include_device_init': board_state.enabled(settings)}
    requested = features.effective_config((ROOT / 'config/aw1000.config').read_text(), options)
    requested = board_state.config(requested, options['include_device_init'])
    (ROOT / 'work/build-features.json').write_text(json.dumps(options, indent=2) + '\n')
    (ROOT / 'work/requested.config').write_text(requested)
    (tree / '.config').write_text(requested)
    # Force a new scan after feed symlinks are installed, even in a container
    # which reuses process IDs (the build system otherwise uses a PID cookie).
    subprocess.run(["make", "-j4", "defconfig"], cwd=tree, check=True,
                   env={**os.environ, "SCAN_COOKIE": uuid.uuid4().hex})
    check_config(tree)


if __name__ == "__main__":
    main()
