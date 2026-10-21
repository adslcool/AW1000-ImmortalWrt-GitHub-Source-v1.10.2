#!/usr/bin/env python3
"""Install the pinned RJ11 sources and the supplied, verified DSP patch."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def check_patch(path=None):
    path = path or ROOT / 'firmware/si3226x/si3226x_patch.bin'
    data = Path(path).read_bytes()
    expected = json.loads((ROOT / 'sources.lock.json').read_text())['aw1000_phone']['patch_sha256']
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected or len(data) != 908 or data[:4] != b'FXSP':
        raise RuntimeError('SLIC patch checksum, size or magic mismatch')
    serial, count = struct.unpack_from('<II', data, 4)
    off = 12 + count * 4 + 32
    support, = struct.unpack_from('<I', data, off)
    if serial != 0x05262017 or count != 191 or support != 16 or len(data) != off + 4 + 6 * support:
        raise RuntimeError('SLIC patch layout mismatch')
    return {'sha256': digest, 'size': len(data), 'serial': hex(serial), 'data_words': count, 'support': support}


def replace_once(text, anchor, replacement):
    if text.count(anchor) != 1:
        raise RuntimeError('Pinned RJ11 source layout changed: ' + anchor[:70])
    return text.replace(anchor, replacement)


def unpack_source(destination):
    lock = json.loads((ROOT / 'sources.lock.json').read_text())['aw1000_phone']
    archive = ROOT / lock['source_archive']
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['source_archive_sha256']:
        raise RuntimeError('RJ11 source archive checksum mismatch')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:xz') as bundle:
        seen = set()
        for member in bundle.getmembers():
            relative = PurePosixPath(member.name)
            if (not member.isfile() or relative.is_absolute() or '..' in relative.parts or
                    member.name in seen or not relative.parts):
                raise RuntimeError('Unsafe RJ11 source archive member')
            seen.add(member.name)
            path = destination.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as stream:
                path.write_bytes(stream.read())
            path.chmod(member.mode & 0o777)
    return destination


def install(tree):
    # Expand only in a temporary directory, never add upload files to the repo.
    with tempfile.TemporaryDirectory(prefix='aw1000-phone-') as temporary:
        return install_source(tree, unpack_source(Path(temporary) / 'source'))


def install_source(tree, vendor):
    lock = json.loads((ROOT / 'sources.lock.json').read_text())['aw1000_phone']
    checks = json.loads((ROOT / 'vendor/phone-source-sha256.json').read_text())
    files = {str(p.relative_to(vendor)) for p in vendor.rglob('*') if p.is_file()}
    # GitHub browser uploads can omit repository metadata. It is never an
    # input to the firmware build; keep strict checks for every source file.
    optional = {'.gitignore'}
    missing = set(checks) - files - optional
    unexpected = files - set(checks)
    if missing or unexpected:
        raise RuntimeError('RJ11 vendored source inventory mismatch: missing=' +
                           repr(sorted(missing)) + ', unexpected=' + repr(sorted(unexpected)))
    for name in sorted(files):
        digest = checks[name]
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('RJ11 vendored source checksum mismatch: ' + name)
    patch = check_patch()
    source = tree / 'aw1000-phone'
    shutil.copytree(vendor, source)
    packages = tree / 'package/custom'
    for name in ('aw1000-fxs', 'fxsd', 'luci-app-fxs'):
        target = packages / name
        shutil.copytree(vendor / 'openwrt' / name, target)
        p = target / 'Makefile'
        text = p.read_text()
        if name != 'luci-app-fxs':
            text = replace_once(text, 'FXS_ROOT ?= $(abspath $(realpath $(CURDIR))/../..)',
                                'FXS_ROOT := $(TOPDIR)/aw1000-phone')
        if name == 'fxsd':
            text = replace_once(text, 'PKG_RELEASE:=1', 'PKG_RELEASE:=2')
            text = replace_once(text, 'DEPENDS:=+kmod-aw1000-fxs',
                                'DEPENDS:=+kmod-aw1000-fxs +ubus-at-daemon +ubus +jq +qmodem')
            text = replace_once(text, '\t$(INSTALL_CONF) ./files/fxs.config $(1)/etc/config/fxs',
                                '\t$(INSTALL_CONF) ./files/fxs.config $(1)/etc/config/fxs\n'
                                '\t$(INSTALL_DATA) ./files/si3226x_patch.bin $(1)/etc/fxs/si3226x_patch.bin\n'
                                '\t$(INSTALL_BIN) ./files/aw1000-phone-* ./files/aw1000-fxsd-run $(1)/usr/sbin/')
        p.write_text(text)
    target = packages / 'fxsd/files'
    shutil.copyfile(ROOT / 'firmware/si3226x/si3226x_patch.bin', target / 'si3226x_patch.bin')
    for name in ('aw1000-phone-port', 'aw1000-phone-at', 'aw1000-phone-setup', 'aw1000-fxsd-run'):
        shutil.copyfile(ROOT / 'files' / name, target / name)
        (target / name).chmod(0o755)
    p = target / 'fxs.config'
    text = replace_once(p.read_text(), "option at_port '/dev/ttyUSB2'", "option at_port 'auto'")
    p.write_text(text + "\n\toption auto_pcm '1'\n")
    p = target / 'fxsd.init'
    text = replace_once(p.read_text(), 'config_get at_port main at_port /dev/ttyUSB2',
                        'config_get at_port main at_port auto')
    text = replace_once(text, 'procd_set_param command /usr/sbin/fxsd -p',
                        'procd_set_param command /usr/sbin/aw1000-fxsd-run "$at_port" -p')
    text = replace_once(text, '-a "$at_port" -l "$volume"', '-l "$volume"')
    if text.count('config_get led main led green:phone') != 2:
        raise RuntimeError('Phone LED default layout changed')
    text = text.replace('config_get led main led green:phone', '''config_get led main led
    if [ -n "$led" ]; then
        [ ! -w "/sys/class/leds/$led/trigger" ] || echo none > "/sys/class/leds/$led/trigger"
        [ ! -w "/sys/class/leds/$led/brightness" ] || echo 0 > "/sys/class/leds/$led/brightness"
    fi''')
    p.write_text(text); p.chmod(0o755)
    # Decode QModem's JSON status once in the queue helper; return raw AT response.
    p = source / 'fxsd/src/modem.c'
    text = replace_once(p.read_text(),
        '"ubus call at-daemon sendat \'{\\"at_port\\":\\"%s\\",\\"at_cmd\\":\\"%s\\",\\"timeout\\":3}\' 2>/dev/null",',
        '"/usr/sbin/aw1000-phone-at \'%s\' \'%s\' 2>/dev/null",')
    p.write_text(text)
    # Keep upstream JS/menu keys intact. Embed a standard LuCI catalog in the
    # application package, whose custom Makefile does not use luci.mk.
    ui = packages / 'luci-app-fxs'
    shutil.copyfile(ROOT / 'files/fxs.zh-cn.po', ui / 'files/fxs.zh-cn.po')
    p = ui / 'Makefile'
    text = replace_once(p.read_text(), 'PKG_RELEASE:=1', 'PKG_RELEASE:=2')
    text = replace_once(text, 'include $(INCLUDE_DIR)/package.mk',
                        'PKG_BUILD_DEPENDS:=luci-base/host\n\ninclude $(INCLUDE_DIR)/package.mk')
    text = replace_once(text, 'define Build/Compile\nendef',
        'define Build/Compile\n\t$(STAGING_DIR_HOSTPKG)/bin/po2lmo ./files/fxs.zh-cn.po $(PKG_BUILD_DIR)/fxs.zh-cn.lmo\nendef')
    text = replace_once(text, 'define Package/luci-app-fxs/install\n',
        'define Package/luci-app-fxs/install\n'
        '\t$(INSTALL_DIR) $(1)/usr/lib/lua/luci/i18n\n'
        '\t$(INSTALL_DATA) $(PKG_BUILD_DIR)/fxs.zh-cn.lmo $(1)/usr/lib/lua/luci/i18n/\n')
    p.write_text(text)
    p = packages / 'luci-app-fxs/files/settings.js'
    text = replace_once(p.read_text(), "o = s.taboption('advanced', form.Value, 'at_port', _('Modem AT port'));",
        "o = s.taboption('advanced', form.Value, 'at_port', _('Modem AT port'), _('Use auto to select the QModem RG500Q-EA USB AT port.'));\n\t\to.placeholder = 'auto';")
    text = replace_once(text, "s.tab('advanced', _('Advanced'));",
        "s.tab('advanced', _('Advanced'));\n\t\to = s.taboption('advanced', form.Flag, 'auto_pcm', _('Automatic PCM setup'), _('Configure modem PCM once on first start. A required modem restart briefly disconnects cellular Internet.'));\n\t\to.default = '1';\n\t\to.rmempty = false;")
    p.write_text(text)
    # LuCI catalogs share a language namespace. Give phone-specific meanings
    # distinct keys rather than overriding global OK/Home/Code/Connected labels.
    for filename, labels in {
        'phone.js': {'OK': 'Normal reading', 'Line': 'Phone line', 'Connected': 'Call connected'},
        'speeddial.js': {'Code': 'Speed dial code', 'Home': 'Home number'}
    }.items():
        p = ui / 'files' / filename
        text = p.read_text()
        for old, new in labels.items():
            key = "_('" + old + "')"
            if key not in text:
                raise RuntimeError('Phone translation label layout changed: ' + old)
            text = text.replace(key, "_('" + new + "')")
        p.write_text(text)
    # Explicitly enable the init script in fresh images. UCI enabled=0 still wins.
    rc = tree / 'files/etc/rc.d/S99fxsd'
    rc.parent.mkdir(parents=True, exist_ok=True)
    rc.symlink_to('../init.d/fxsd')
    changed = [source / 'fxsd/src/modem.c']
    for name in ('aw1000-fxs', 'fxsd', 'luci-app-fxs'):
        changed += sorted(p for p in (packages / name).rglob('*') if p.is_file())
    return {'commit': lock['commit'], 'patch': patch,
            'adapted_files': {str(p.relative_to(tree)): hashlib.sha256(p.read_bytes()).hexdigest() for p in changed}}
