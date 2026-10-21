import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import compat


class RawIP(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.base = self.path / "net/wwan0"
        (self.base / "qmi").mkdir(parents=True)
        (self.base / "device").mkdir()
        driver = self.path / "drivers/qmi_wwan"
        driver.mkdir(parents=True)
        (self.base / "device/driver").symlink_to(driver)
        (self.base / "qmi/raw_ip").write_text("N\n")
        (self.base / "flags").write_text("0x1003\n")
        self.board = self.path / "board_name"
        self.board.write_text("arcadyan,aw1000\n")
        self.config = self.path / "uci.json"
        self.config.write_text(json.dumps({"qmodem.4_1.name": "RG500Q-EA",
            "qmodem.4_1.data_interface": "usb", "qmodem.4_1.host_ipv4_owner": "netifd"}))
        self.commands = self.path / "commands"
        self.commands.write_text("")
        binpath = self.path / "bin"
        binpath.mkdir()
        (binpath / "uci").write_text('''#!/usr/bin/env python3
import json, os, sys
value = json.load(open(os.environ['UCI_CONFIG'])).get(sys.argv[-1])
if value is None: sys.exit(1)
print(value)
''')
        (binpath / "ip").write_text('''#!/usr/bin/env python3
import os, sys
from pathlib import Path
assert sys.argv[1:5] == ['link', 'set', 'dev', 'wwan0']
action = sys.argv[5]
with open(os.environ['COMMANDS'], 'a') as f: f.write(action + '\\n')
if action == 'down' and os.environ.get('FAIL_DOWN') == '1': sys.exit(8)
Path(os.environ['FLAGS']).write_text('0x1003' if action == 'up' else '0x1002')
''')
        (binpath / "logger").write_text("#!/bin/sh\nexit 0\n")
        for p in binpath.iterdir():
            p.chmod(0o755)
        source = (ROOT / "files/aw1000-qmi-prepare.sh").read_text()
        source = source.replace("/tmp/sysinfo/board_name", str(self.board))
        source = source.replace("/sys/class/net/", str(self.path / "net") + "/")
        self.script = self.path / "prepare.sh"
        self.script.write_text(source)
        self.env = {**os.environ, "PATH": str(binpath) + ":" + os.environ["PATH"],
                    "UCI_CONFIG": str(self.config), "COMMANDS": str(self.commands),
                    "FLAGS": str(self.base / "flags")}

    def run_helper(self, device="wwan0"):
        return subprocess.run(["sh", str(self.script), "4_1", device],
                              env=self.env, capture_output=True, text=True)

    def test_raw_ip_is_enabled_with_previous_link_state_restored(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.base / "qmi/raw_ip").read_text().strip(), "Y")
        self.assertEqual(self.commands.read_text().splitlines(), ["down", "up"])
        self.assertEqual(int((self.base / "flags").read_text(), 16) & 1, 1)
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.commands.read_text().splitlines(), ["down", "up"])

    def test_administratively_down_device_is_not_brought_up(self):
        (self.base / "flags").write_text("0x1002")
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.commands.read_text().splitlines(), ["down"])

    def test_other_board_modem_driver_and_owner_are_not_modified(self):
        original_config = json.loads(self.config.read_text())
        for key, value in (("board", "other,board"), ("qmodem.4_1.name", "other-modem"),
                           ("qmodem.4_1.host_ipv4_owner", "legacy"),
                           ("qmodem.4_1.data_interface", "pcie"), ("driver", "cdc_mbim")):
            with self.subTest(key=key):
                self.board.write_text(value if key == "board" else "arcadyan,aw1000")
                data = dict(original_config)
                if key not in ("board", "driver"):
                    data[key] = value
                self.config.write_text(json.dumps(data))
                link = self.base / "device/driver"
                link.unlink()
                target = self.path / "drivers" / (value if key == "driver" else "qmi_wwan")
                target.mkdir(exist_ok=True)
                link.symlink_to(target)
                self.assertEqual(self.run_helper().returncode, 0)
                self.assertEqual(self.commands.read_text(), "")
                self.assertEqual((self.base / "qmi/raw_ip").read_text().strip(), "N")

    def test_link_failure_is_reported_and_restoration_attempted(self):
        self.env["FAIL_DOWN"] = "1"
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.commands.read_text().splitlines(), ["down", "up"])
        self.assertEqual((self.base / "qmi/raw_ip").read_text().strip(), "N")

    def test_invalid_device_is_rejected_before_any_mutation(self):
        self.assertEqual(self.run_helper("../wan").returncode, 2)
        self.assertEqual(self.commands.read_text(), "")

    def test_dial_patch_is_guarded_and_runs_inside_each_retry(self):
        source = 'qmi_dial() {\n    while true; do\n        m_debug "dialing: $cmd_line"\n        $cmd_line &\n    done\n}\n'
        patched = compat.modem_raw_ip_patch(source)
        self.assertEqual(compat.modem_raw_ip_patch(patched), patched)
        self.assertLess(patched.index('while true'), patched.index('/usr/sbin/aw1000-qmi-prepare'))
        self.assertLess(patched.index('/usr/sbin/aw1000-qmi-prepare'), patched.index('$cmd_line &'))
        with self.assertRaises(RuntimeError):
            compat.modem_raw_ip_patch("changed source")
