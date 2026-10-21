#!/usr/bin/env python3
"""Validate untrusted dispatch inputs; never emit credentials in diagnostics."""
import ipaddress
import features
import board_state
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def mask(value):
    if value and os.getenv("GITHUB_ACTIONS") == "true":
        value = value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print("::add-mask::" + value, flush=True)


def validate(env):
    admin = env.get("ADMIN_PASSWORD", "")
    wifi = env.get("WIFI_PASSWORD") or "1234567890"
    master = env.get('BOARD_STATE_KEY', '').strip() or board_state.DEFAULT_KEY_HEX
    # Password source alone determines package selection; stale flags cannot override it.
    include_init = not bool(admin)
    for value in (admin, wifi, master):
        mask(value)
    if include_init and not re.fullmatch(r'[0-9a-fA-F]{64}', master):
        raise ValueError('启用初始化组件时，BOARD_STATE_KEY 必须为64位十六进制')
    try:
        ip = ipaddress.IPv4Address(env.get("LAN_IP") or "192.168.1.1")
    except ipaddress.AddressValueError:
        raise ValueError("LAN_IP 必须是有效的 IPv4 地址") from None
    if not ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValueError("LAN_IP 必须是可用的局域网地址")
    if not any(ip in n for n in (ipaddress.IPv4Network("10.0.0.0/8"),
                                ipaddress.IPv4Network("172.16.0.0/12"),
                                ipaddress.IPv4Network("192.168.0.0/16"))):
        raise ValueError("LAN_IP 必须属于 RFC1918 私网")
    if int(ip) & 255 in (0, 255):
        raise ValueError("LAN_IP 在 /24 网络中不能以 .0 或 .255 结尾")
    if admin and (not 8 <= len(admin.encode("utf-8")) <= 128 or any(ord(c) < 32 or ord(c) == 127 for c in admin)):
        raise ValueError("ADMIN_PASSWORD 需为 8–128 字节，不能包含控制字符；也可设置仓库 Secret")
    if not 8 <= len(wifi) <= 63 or any(not 32 <= ord(c) <= 126 for c in wifi):
        raise ValueError("WIFI_PASSWORD 需为 8–63 个可打印 ASCII 字符；也可设置仓库 Secret")
    ssids = {}
    for key, fallback in (("SSID_24G", "AW1000_2.4G"), ("SSID_5G", "AW1000_5G")):
        value = env.get(key, fallback)
        if not 1 <= len(value.encode("utf-8")) <= 32 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError(key + " 需为 1–32 字节，不能包含控制字符")
        ssids[key.lower()] = value
    country = 'CN'
    choices = {}
    for key in features.PROXY_DEFAULTS:
        raw = env.get(key.upper(), 'true').lower()
        if raw not in ('true', 'false'):
            raise ValueError(key.upper() + ' must be true or false')
        choices[key] = raw == 'true'
    return {"lan_ip": str(ip), "netmask": "255.255.255.0", "country_code": country,
            "wifi_password": wifi, "include_device_init": include_init,
            "board_state_key": master if include_init else '', **ssids, **choices}, admin


def password_hash(password):
    # stdin prevents leaking the password through argv or process listings.
    result = subprocess.run(["openssl", "passwd", "-6", "-salt", secrets.token_hex(8), "-stdin"],
                            input=password + "\n", text=True, capture_output=True, check=True)
    hashed = result.stdout.strip()
    if not re.fullmatch(r"\$6\$[A-Za-z0-9./]+\$[A-Za-z0-9./]+", hashed):
        raise RuntimeError("生成管理员密码哈希失败")
    mask(hashed)
    return hashed


def main():
    try:
        settings, admin = validate(os.environ)
        # IMEI mode has no shared initial root password; unlock only after initialization.
        settings["root_password_hash"] = '!' if board_state.enabled(settings) else password_hash(admin)
    except (ValueError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
    directory = ROOT / ".private"
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = directory / "settings.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(settings, stream, ensure_ascii=False)
    os.chmod(path, 0o600)
    print("LAN、SSID、固定CN地区和组件选择校验通过；凭据仅保存至私有构建目录。")


if __name__ == "__main__":
    main()
