import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


settings = load("settings")
prepare = load("prepare")
configure = load("configure")


def valid_env(**overrides):
    return {"INCLUDE_DEVICE_INIT": "false", "LAN_IP": "192.168.50.1", "ADMIN_PASSWORD": "Test Admin'42$",
            "WIFI_PASSWORD": "Common Wifi'42$", "SSID_24G": "测试 2G", "SSID_5G": "Test 5G",
            "COUNTRY_CODE": "CN", **overrides}


class Inputs(unittest.TestCase):
    def test_valid_settings_and_password_hash(self):
        value, password = settings.validate(valid_env())
        self.assertEqual(value["netmask"], "255.255.255.0")
        hashed = settings.password_hash(password)
        self.assertNotIn(password, hashed)
        salt = hashed.split("$")[2]
        check = subprocess.run(["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
                               input=password + "\n", capture_output=True, text=True, check=True)
        self.assertEqual(hashed, check.stdout.strip())

    def test_ip_must_be_a_usable_private_host(self):
        for ip in ("8.8.8.8", "127.0.0.1", "192.168.1.0", "192.168.1.255", "0.0.0.0",
                   "224.0.0.1", "169.254.1.1", "10.1.2.300", "10.1.2.3; touch /tmp/x"):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                settings.validate(valid_env(LAN_IP=ip))

    def test_ssid_limits_are_utf8_bytes(self):
        settings.validate(valid_env(SSID_24G="测" * 10))
        for ssid in ("测" * 11, "", "line\nfeed", "null\0byte"):
            with self.subTest(ssid=ssid), self.assertRaises(ValueError):
                settings.validate(valid_env(SSID_24G=ssid))

    def test_password_constraints(self):
        for key, values in (("ADMIN_PASSWORD", ("short", "test\nsecret")),
                            ("WIFI_PASSWORD", ("short", "密" * 10, "a" * 64, "1234567\n"))):
            for value in values:
                with self.subTest(key=key), self.assertRaises(ValueError):
                    settings.validate(valid_env(**{key: value}))

    def test_country_is_fixed_cn(self):
        value, _ = settings.validate(valid_env(COUNTRY_CODE="DE"))
        self.assertEqual(value['country_code'], 'CN')

    def test_numeric_latest_stable_tag(self):
        output = "\n".join("a" * 40 + "\trefs/tags/" + tag for tag in
                           ("v24.10.6", "v25.12.2", "v25.12.10", "v26.1.0-rc1", "v25.12-SNAPSHOT"))
        self.assertEqual(prepare.stable_tag("latest", output), "v25.12.10")
        self.assertEqual(prepare.stable_tag("25.12.2"), "v25.12.2")
        for ref in ("main", "v25.12.0-rc1", "latest; id", "../ref"):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                prepare.stable_tag(ref)


class Provisioning(unittest.TestCase):
    def test_onboarding_intent_waits_for_completed_provisioning(self):
        value = {**self.settings, 'include_device_init': True,
                 'board_state_key': bytes(range(32)).hex(), 'root_password_hash': '!'}
        configure.overlay(self.tree, value)
        self.assertEqual(self.run_provision().returncode, 0)
        etc = self.tree/'files/etc'
        self.assertTrue((etc/'aw1000-board-state/requested').is_file())
        self.assertTrue((etc/'aw1000-provisioned').is_file())
        self.assertIn('root:!:', (etc/'shadow').read_text())
        (etc/'shadow').write_text('root:USER_CHANGED:0:0:99999:7:::\n')
        self.assertEqual(self.run_provision().returncode, 0)
        self.assertIn('USER_CHANGED', (etc/'shadow').read_text())

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.tree = self.path / "tree"
        value, _ = settings.validate(valid_env())
        value["root_password_hash"] = "$6$testsalt$TESTHASH"
        self.settings = value
        configure.overlay(self.tree, value)
        etc = self.tree / "files/etc"
        (etc / "shadow").write_text("root:OLD:0:0:99999:7:::\nnobody:*:0:0:99999:7:::\n")
        (etc / "sysupgrade.conf").write_text("/etc/custom\n")
        (self.path / "board_name").write_text("arcadyan,aw1000\n")
        # This UCI boundary double stores actual command arguments, preserving
        # spaces, quotes and shell syntax, and supplies reversed radio ordering.
        self.state = self.path / "uci.json"
        self.state.write_text(json.dumps({
            "wireless.radio0": "wifi-device", "wireless.radio0.band": "5g",
            "wireless.radio1": "wifi-device", "wireless.radio1.band": "2g",
            "wireless.default_radio0": "wifi-iface", "wireless.default_radio0.device": "radio0",
            "wireless.default_radio1": "wifi-iface", "wireless.default_radio1.device": "radio1"}))
        binaries = self.path / "bin"
        binaries.mkdir()
        mock = binaries / "uci"
        mock.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ["MOCK_UCI"])
state = json.loads(path.read_text())
args = sys.argv[1:]
if args and args[0] == "-q": args.pop(0)
cmd, *rest = args
if cmd == "show":
 for k,v in state.items():
  if k.startswith(rest[0] + ".") and k.count(".") == 1:
   print(k + "=" + v)
elif cmd == "get":
 if rest[0] not in state: sys.exit(1)
 value=state[rest[0]]
 print(" ".join(value) if isinstance(value,list) else value)
elif cmd == "set":
 key,value = rest[0].split("=",1)
 if key == os.environ.get("FAIL_UCI_SET"): sys.exit(8)
 state[key]=value
elif cmd == "delete":
 state={k:v for k,v in state.items() if k != rest[0] and not k.startswith(rest[0]+".")}
elif cmd == "add_list":
 key,value = rest[0].split("=",1)
 state.setdefault(key,[]).append(value)
elif cmd != "commit": sys.exit(2)
path.write_text(json.dumps(state))
''')
        mock.chmod(0o755)
        for name in ("logger", "ubus", "wifi"):
            p = binaries / name
            p.write_text("#!/bin/sh\nexit 0\n")
            p.chmod(0o755)
        self.env = {**os.environ, "PATH": str(binaries) + ":" + os.environ["PATH"],
                    "MOCK_UCI": str(self.state), "AW1000_LED_STATUS": str(self.path/"led-status.json")}
        script = (ROOT / "files/aw1000-provision.sh").read_text()
        script = script.replace("/etc/", str(etc) + "/")
        script = script.replace("/tmp/sysinfo/board_name", str(self.path / "board_name"))
        script = script.replace("/sysupgrade.tgz", str(self.path / "sysupgrade.tgz"))
        script = script.replace("/tmp/sysupgrade.tar", str(self.path / "sysupgrade.tar"))
        script = script.replace("/sbin/wifi", str(binaries / "wifi"))
        self.script = self.path / "provision.sh"
        self.script.write_text(script)
        self.script.chmod(0o755)

    def run_provision(self):
        return subprocess.run(["sh", str(self.script), "once"], env=self.env,
                              capture_output=True, text=True)

    def test_reversed_radios_receive_correct_bands_and_shared_password(self):
        result = self.run_provision()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["wireless.radio0.channel"], "149")
        self.assertEqual(state["wireless.radio1.channel"], "auto")
        self.assertEqual(state["wireless.aw1000_2g.device"], "radio1")
        self.assertEqual(state["wireless.aw1000_5g.device"], "radio0")
        self.assertEqual(state["wireless.aw1000_2g.key"], self.settings["wifi_password"])
        self.assertEqual(state["wireless.aw1000_5g.key"], self.settings["wifi_password"])
        self.assertEqual(state["wireless.aw1000_2g.ssid"], self.settings["ssid_24g"])
        self.assertNotIn("wireless.default_radio0", state)
        self.assertNotIn("wireless.default_radio1", state)
        etc = self.tree / "files/etc"
        self.assertIn("root:" + self.settings["root_password_hash"], (etc / "shadow").read_text())
        self.assertIn("nobody:*:", (etc / "shadow").read_text())
        self.assertFalse((etc / "aw1000-build.conf").exists())
        self.assertTrue((etc / "aw1000-provisioned").exists())

    def test_wifi_may_appear_after_the_first_attempt(self):
        state = json.loads(self.state.read_text())
        original = dict(state)
        self.state.write_text(json.dumps({k:v for k,v in state.items() if not k.startswith("wireless")}))
        self.assertNotEqual(self.run_provision().returncode, 0)
        self.assertFalse((self.tree / "files/etc/aw1000-provisioned").exists())
        partial = json.loads(self.state.read_text())
        self.assertEqual(partial["network.lan.ipaddr"], self.settings["lan_ip"])
        self.state.write_text(json.dumps({**partial, **original}))
        self.assertEqual(self.run_provision().returncode, 0)

    def test_keep_settings_does_not_reset_lan_or_password(self):
        self.assertEqual(self.run_provision().returncode, 0)
        state = json.loads(self.state.read_text())
        state["network.lan.ipaddr"] = "192.168.88.1"
        self.state.write_text(json.dumps(state))
        etc = self.tree / "files/etc"
        (etc / "shadow").write_text("root:USER_CHANGED:0:0:99999:7:::\n")
        configure.overlay(self.tree, self.settings)
        self.assertEqual(self.run_provision().returncode, 0)
        self.assertEqual(json.loads(self.state.read_text())["network.lan.ipaddr"], "192.168.88.1")
        self.assertIn("root:USER_CHANGED:", (etc / "shadow").read_text())

    def test_shell_syntax_in_ssid_and_password_remains_literal(self):
        sentinel = self.path / "must-not-exist"
        value = dict(self.settings, ssid_24g="'$(touch INJECTION)'",
                     wifi_password="'$(touch " + str(sentinel) + ")'")
        configure.overlay(self.tree, value)
        result = self.run_provision()
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state.read_text())
        self.assertEqual(state["wireless.aw1000_2g.key"], value["wifi_password"])
        self.assertEqual(state["wireless.aw1000_2g.ssid"], value["ssid_24g"])
        self.assertFalse(sentinel.exists())

    def test_wrong_device_is_rejected(self):
        (self.path / "board_name").write_text("some,other-device\n")
        self.assertNotEqual(self.run_provision().returncode, 0)
        self.assertFalse((self.tree / "files/etc/aw1000-provisioned").exists())

    def test_configuration_failure_does_not_mark_provisioning_complete(self):
        self.env["FAIL_UCI_SET"] = "wireless.aw1000_5g.key"
        self.assertNotEqual(self.run_provision().returncode, 0)
        self.assertFalse((self.tree / "files/etc/aw1000-provisioned").exists())
        self.assertTrue((self.tree / "files/etc/aw1000-build.conf").exists())

    def test_keep_settings_migration_from_another_firmware(self):
        (self.path / "sysupgrade.tgz").touch()
        state = json.loads(self.state.read_text())
        state["network.lan.ipaddr"] = "10.20.30.1"
        self.state.write_text(json.dumps(state))
        self.assertEqual(self.run_provision().returncode, 0)
        self.assertEqual(json.loads(self.state.read_text())["network.lan.ipaddr"], "10.20.30.1")
        self.assertIn("root:OLD:", (self.tree / "files/etc/shadow").read_text())
        self.assertTrue((self.tree / "files/etc/aw1000-provisioned").exists())


if __name__ == "__main__":
    unittest.main()

class ProxyBuildInputs(unittest.TestCase):
    def test_boolean_inputs_preserved(self):
        for ssr in ('true','false'):
            for oc in ('true','false'):
                value,_ = settings.validate(valid_env(INCLUDE_SSR_PLUS=ssr, INCLUDE_OPENCLASH=oc))
                self.assertEqual(value['include_ssr_plus'],ssr=='true')
                self.assertEqual(value['include_openclash'],oc=='true')
        with self.assertRaises(ValueError):
            settings.validate(valid_env(INCLUDE_OPENCLASH='nope'))
