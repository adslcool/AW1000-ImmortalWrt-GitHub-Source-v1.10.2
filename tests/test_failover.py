import json
import subprocess
import unittest

import test_project as project

ROOT = project.ROOT
load = project.load

compat = load("compat")


class Failover(unittest.TestCase):
    setUp = project.Provisioning.setUp

    def initialize(self):
        # Reuse the established UCI boundary fixture without inheriting its tests.
        self.state_path = self.state
        current = json.loads(self.state_path.read_text())
        current.update({"network.wan": "interface", "network.wan.proto": "pppoe",
                        "network.wan.username": "existing-pppoe-user", "network.wan6": "interface",
                        "mwan3.globals.aw1000_enabled": "1", "qmodem.main.enable_dial": "1",
                        "qmodem.usb_internal": "modem-device", "qmodem.usb_internal.name": "RG500Q-EA",
                        "qmodem.usb_internal.alias": "wwan0", "qmodem.usb_internal.data_interface": "usb", "qmodem.usb_internal.apn": "custom-apn",
                        "dhcp.lan": "dhcp", "dhcp.lan.interface": "lan"})
        self.state_path.write_text(json.dumps(current))
        self.log = self.path / "services.log"
        for name in ("qmodem-uplink", "mwan3", "aw1000-mwan-follow", "qmodem_monitor", "qmodem_network", "odhcpd"):
            file = self.path / "bin" / name
            file.write_text("#!/bin/sh\nprintf '%s %s\\n' '" + name + "' \"$*\" >> \"$SERVICE_TEST_LOG\"\n")
            file.chmod(0o755)
        template=self.path / "share/aw1000/mwan3.default"
        template.parent.mkdir(parents=True)
        template.write_text((ROOT / "files/mwan3.config").read_text())
        (self.path / "locks").mkdir()
        self.env["SERVICE_TEST_LOG"] = str(self.log)

    def run_file(self, name, *args):
        content = (ROOT / "files" / name).read_text()
        content = content.replace("/etc/", str(self.tree / "files/etc") + "/")
        for service in ("qmodem-uplink", "mwan3", "aw1000-mwan-follow", "qmodem_monitor", "qmodem_network", "odhcpd"):
            content = content.replace(str(self.tree / "files/etc/init.d" / service), str(self.path / "bin" / service))
        content = content.replace("/tmp/sysinfo/board_name", str(self.path / "board_name"))
        content = content.replace("/var/lock", str(self.path / "locks"))
        content = content.replace("/usr/share/aw1000", str(self.path / "share/aw1000"))
        content = content.replace("/var/run/qmodem", str(self.path / "run/qmodem"))
        content = content.replace("/usr/sbin/aw1000-leds", str(self.path / "bin/logger"))
        script = self.path / name
        script.write_text(content)
        return subprocess.run(["sh", str(script), *args], env=self.env, text=True, capture_output=True)

    def test_missing_instance_alias_registers_native_logical_interface(self):
        self.initialize()
        config=json.loads(self.state_path.read_text())
        config.pop('qmodem.usb_internal.alias')
        config.update({'qmodem.usb0':'modem-slot','qmodem.usb0.alias':'wwan0'})
        self.state_path.write_text(json.dumps(config))
        result=self.run_file('aw1000-uplink-enroll.sh','usb_internal')
        self.assertEqual(result.returncode,0,result.stderr)
        config=json.loads(self.state_path.read_text())
        self.assertEqual(config['mwan3.globals.aw1000_modem'],'usb_internal')
        self.assertEqual(config['mwan3.globals.aw1000_cellular'],'usb_internal')
        self.assertEqual(config['mwan3.aw_cell.interface'],'usb_internal')
        self.assertEqual(config['qmodem.usb_internal.apn'],'custom-apn')
        self.assertNotIn('qmodem.usb_internal.alias',config)
        self.assertEqual(config['qmodem.usb0.alias'],'wwan0')

    def test_phone_led_upgrade_is_independent_and_preserves_ipv6_baseline(self):
        self.initialize()
        etc=self.tree/'files/etc'
        for marker in ('aw1000-v160','aw1000-led-v161','aw1000-internet-v170'):(etc/marker).touch()
        config=json.loads(self.state_path.read_text())
        config.update({'fxs.main':'fxs','fxs.main.led':'','aw1000.ipv6.original_classes':'saved-native-prefix'})
        self.state_path.write_text(json.dumps(config))
        result=self.run_file('92-aw1000-v160')
        self.assertEqual(result.returncode,0,result.stderr)
        config=json.loads(self.state_path.read_text())
        self.assertEqual(config['fxs.main.led'],'green:phone')
        self.assertEqual(config['aw1000.ipv6.original_classes'],'saved-native-prefix')
        self.assertTrue((etc/'aw1000-phone-led-v175').exists())
        config['fxs.main.led']='';self.state_path.write_text(json.dumps(config))
        self.assertEqual(self.run_file('92-aw1000-v160').returncode,0)
        self.assertEqual(json.loads(self.state_path.read_text())['fxs.main.led'],'')

    def test_wired_first_preserves_pppoe_and_initializes_only_once(self):
        self.initialize()
        result = self.run_file("91-aw1000-failover")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["network.wan.proto"], "pppoe")
        self.assertEqual(state["network.wan.username"], "existing-pppoe-user")
        self.assertEqual(state["network.wan.metric"], "10")
        self.assertEqual(state["qmodem_uplink.main.enabled"], "0")
        self.assertEqual(state["firewall.@defaults[0].flow_offloading"], "0")
        self.assertEqual(state["firewall.@defaults[0].flow_offloading_hw"], "0")
        state["qmodem_uplink.main.interval"] = "20"
        self.state_path.write_text(json.dumps(state))
        self.assertEqual(self.run_file("91-aw1000-failover").returncode, 0)
        self.assertEqual(json.loads(self.state_path.read_text())["qmodem_uplink.main.interval"], "20")

    def test_modem_is_dialed_and_monitored_while_wired_is_preferred(self):
        self.initialize()
        self.assertEqual(self.run_file("91-aw1000-failover").returncode, 0)
        result = self.run_file("aw1000-uplink-enroll.sh", "usb_internal")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["qmodem.usb_internal.enable_dial"], "1")
        self.assertEqual(state["qmodem.usb_internal.host_ipv4_owner"], "netifd")
        self.assertEqual(state["qmodem.usb_internal.metric"], "200")
        self.assertEqual(state["qmodem.usb_internal.monitor_recovery_action"], "redial")
        self.assertEqual(state["qmodem.usb_internal.apn"], "custom-apn")
        self.assertEqual(state["qmodem.usb_internal.pdp_type"], "ipv4v6")
        self.assertEqual(state["qmodem.usb_internal.extend_prefix"], "1")
        self.assertEqual(state["qmodem.usb_internal.ra_master"], "0")
        self.assertEqual(state["network.lan.ip6assign"], "64")
        self.assertEqual(state["dhcp.lan.ra"], "server")
        self.assertEqual(state["mwan3.aw_cell.interface"], "wwan0")
        self.assertEqual(state["mwan3.globals.aw1000_modem"], "usb_internal")
        self.assertEqual(state["mwan3.wwan0.enabled"], "1")
        # Repeated scans must not hang/restart a healthy PDP or re-enable user changes.
        before = self.log.read_text()
        state["qmodem.usb_internal.enable_dial"] = "0"
        self.state_path.write_text(json.dumps(state))
        self.assertEqual(self.run_file("aw1000-uplink-enroll.sh", "usb_internal").returncode, 0)
        self.assertEqual(self.log.read_text(), before)
        self.assertEqual(json.loads(self.state_path.read_text())["qmodem.usb_internal.enable_dial"], "0")

    def test_old_ipv4_install_migrates_once_without_changing_ipv4_policy_or_apn(self):
        self.initialize()
        state = json.loads(self.state_path.read_text())
        state.update({"qmodem.usb_internal.aw1000_uplink_initialized": "1",
                      "qmodem.usb_internal.pdp_type": "ip",
                      "qmodem.usb_internal.enable_dial": "0",
                      "qmodem_uplink.main.interval": "17",
                      "network.lan.ip6assign": "60", "dhcp.lan.ra": "relay"})
        self.state_path.write_text(json.dumps(state))
        pid = self.path / "run/qmodem/usb_internal_dir/usb_internal.pid"
        pid.parent.mkdir(parents=True)
        pid.write_text("12345\n")
        result = self.run_file("aw1000-uplink-enroll.sh", "usb_internal")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads(self.state_path.read_text())
        self.assertEqual(after["qmodem.usb_internal.pdp_type"], "ipv4v6")
        self.assertEqual(after["qmodem.usb_internal.enable_dial"], "0")
        self.assertEqual(after["qmodem.usb_internal.apn"], "custom-apn")
        self.assertEqual(after["qmodem_uplink.main.interval"], "17")
        self.assertEqual(after["network.lan.ip6assign"], "60")
        self.assertEqual(after["dhcp.lan.ra"], "relay")
        before_log = self.log.read_text()
        self.assertIn("qmodem_network reconfigure usb_internal", before_log)
        after["qmodem.usb_internal.pdp_type"] = "ipv6"
        self.state_path.write_text(json.dumps(after))
        self.assertEqual(self.run_file("aw1000-uplink-enroll.sh", "usb_internal").returncode, 0)
        self.assertEqual(self.log.read_text(), before_log)
        self.assertEqual(json.loads(self.state_path.read_text())["qmodem.usb_internal.pdp_type"], "ipv6")

    def test_v160_migration_preserves_network_credentials_and_runs_once(self):
        self.initialize()
        c=json.loads(self.state_path.read_text())
        c.update({'network.lan.ip6class':['local','custom6'], 'dhcp.lan.ra_default':'1',
                  'dhcp.lan.ra_lifetime':'900','network.lan.ip6assign':'60'})
        self.state_path.write_text(json.dumps(c))
        result=self.run_file('92-aw1000-v160')
        self.assertEqual(result.returncode,0,result.stderr)
        c=json.loads(self.state_path.read_text())
        self.assertEqual(c['aw1000.ipv6.original_classes'],'local custom6')
        self.assertEqual(c['aw1000.ipv6.original_ra_lifetime'],'900')
        self.assertEqual(c['network.wan.username'],'existing-pppoe-user')
        self.assertEqual(c['qmodem.usb_internal.apn'],'custom-apn')
        self.assertEqual(c['network.lan.ip6assign'],'64')
        self.assertEqual(c['firewall.@defaults[0].flow_offloading'],'0')
        self.assertEqual(self.run_file('92-aw1000-v160').returncode,0)
        self.assertEqual(json.loads(self.state_path.read_text())['aw1000.ipv6.original_classes'],'local custom6')

    def test_other_modems_and_disabled_manager_are_left_unchanged(self):
        self.initialize()
        state = json.loads(self.state_path.read_text())
        state["qmodem.usb_other"] = "modem-device"
        state["qmodem.usb_other.name"] = "RM520N-GL"
        self.state_path.write_text(json.dumps(state))
        self.assertEqual(self.run_file("aw1000-uplink-enroll.sh", "usb_other").returncode, 0)
        self.assertNotIn("qmodem_uplink.cellular", json.loads(self.state_path.read_text()))
        state["mwan3.globals.aw1000_enabled"] = "0"
        self.state_path.write_text(json.dumps(state))
        self.assertEqual(self.run_file("aw1000-uplink-enroll.sh", "usb_internal").returncode, 0)
        self.assertFalse(self.log.exists())


class Compatibility(unittest.TestCase):
    def test_tproxy_route_still_matches_after_uplink_mark_is_added(self):
        self.assertEqual(compat.SSR_MASK & compat.UPLINK_MASK, 0)
        for member_id in (1, 2, 63):
            combined = compat.SSR_MARK | (member_id << 16)
            self.assertEqual(combined & compat.SSR_MASK, compat.SSR_MARK)
            self.assertEqual(combined & compat.UPLINK_MASK, member_id << 16)

    def test_guarded_patches_are_idempotent_and_cover_route_cleanup(self):
        original = 'FWMARK="0x53535250"\n' + '\n'.join([
            'ip rule del fwmark ${FWMARK} table 999', 'ip rule del fwmark ${FWMARK} table 999',
            'ip rule add fwmark ${FWMARK} table 999 priority 999',
            'ip rule add fwmark ${FWMARK} table 999 priority 999',
            'ip rule add fwmark "$FWMARK" table 999 priority 999'])
        patched = compat.ssr_mark_patch(original)
        self.assertEqual(patched.count('/0x0000ffff'), 5)
        self.assertEqual(compat.ssr_mark_patch(patched), patched)
        with self.assertRaises(RuntimeError):
            compat.ssr_mark_patch('FWMARK="unknown"')
        hook = 'config_section=$1\ninit_type=$2\ncase $init_type in\n'
        self.assertEqual(compat.modem_hook_patch(compat.modem_hook_patch(hook)), compat.modem_hook_patch(hook))


if __name__ == "__main__":
    unittest.main()
