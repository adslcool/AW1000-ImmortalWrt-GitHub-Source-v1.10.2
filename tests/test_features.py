import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('features', ROOT / 'scripts/features.py')
features = importlib.util.module_from_spec(spec)
spec.loader.exec_module(features)


class FeaturesTest(unittest.TestCase):
    def test_button_press_release_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            # Substitute paths only; execute the actual generated shell logic.
            body = features.BUTTON.replace('/var/run', str(root)).replace(
                '/sbin/wifi', str(root / 'wifi')).replace('/usr/sbin/aw1000-leds', '/bin/true')
            (root / 'wifi').write_text('#!/bin/sh\necho "$1" >> "' + str(root / 'calls') + '"\n')
            (root / 'wifi').chmod(0o755)
            script = root / 'button'
            script.write_text(body)
            def event(action):
                subprocess.run(['sh', str(script)], env={**os.environ, 'ACTION': action}, check=True)
            event('pressed')
            self.assertFalse((root / 'calls').exists())
            event('released')
            self.assertTrue((root / 'aw1000-wifi-off').exists())
            event('released')
            self.assertFalse((root / 'aw1000-wifi-off').exists())
            self.assertEqual((root / 'calls').read_text(), 'down\nup\n')
            self.assertNotIn('uci ', features.BUTTON)
            self.assertIn('disabled=0', features.BOOT)
            self.assertIn('rm -f /var/run/aw1000-wifi-off', features.BOOT)

    def test_boot_clears_temporary_off_and_enables_both_radios(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            calls = root / 'calls'
            (root / 'aw1000-wifi-off').touch()
            functions = root / 'functions'
            functions.write_text('''config_load() { :; }
config_foreach() {
    case "$2" in
        wifi-device) "$1" radio0; "$1" radio1;;
        wifi-iface) "$1" aw1000_2g; "$1" aw1000_5g;;
    esac
}
config_get() {
    case "$3" in mode) eval "$1=ap";; encryption) eval "$1=psk2+ccmp";; esac
}
''')
            wifi = root / 'wifi'
            wifi.write_text('#!/bin/sh\necho "wifi $*" >> "' + str(calls) + '"\n')
            wifi.chmod(0o755)
            script = features.BOOT.replace('/lib/functions.sh', str(functions)).replace('/var/run', str(root)).replace('/sbin/wifi', str(wifi))
            script += '\nuci() { echo "uci $*" >> "' + str(calls) + '"; }\nstart\n'
            subprocess.run(['sh', '-c', script], check=True)
            result = calls.read_text()
            self.assertIn('wireless.radio0.disabled=0', result)
            self.assertIn('wireless.radio1.disabled=0', result)
            self.assertIn('wireless.aw1000_2g.wps_pushbutton=1', result)
            self.assertIn('wireless.aw1000_5g.wps_pushbutton=1', result)
            self.assertTrue(result.endswith('wifi up\n'))
            self.assertFalse((root / 'aw1000-wifi-off').exists())

    def test_benchmark_success_removes_only_its_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'etc/crontabs').mkdir(parents=True)
            cron = root / 'etc/crontabs/root'
            own = '0 5 * * * /usr/sbin/aw1000-coremark\n'
            other = '0 3 * * * /usr/sbin/other-task\n'
            cron.write_text(own + other)
            body = features.COREMARK.replace('crontab /etc/crontabs/root', 'true').replace('/tmp', str(root)).replace('/var/run', str(root)).replace('/etc/crontabs', str(root / 'etc/crontabs')).replace('/etc/init.d/cron restart', 'true').replace('/bin/coremark', '/bin/echo')
            subprocess.run(['sh', '-c', body], check=True)
            self.assertTrue((root / 'coremark.log').is_file())
            self.assertEqual(cron.read_text(), other)
            self.assertIn('0 5 * * *', features.DEFAULTS)

    def test_benchmark_failure_keeps_schedule(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'etc/crontabs').mkdir(parents=True)
            cron = root / 'etc/crontabs/root'
            line = '0 5 * * * /usr/sbin/aw1000-coremark\n'
            cron.write_text(line)
            body = features.COREMARK.replace('crontab /etc/crontabs/root', 'true').replace('/tmp', str(root)).replace('/var/run', str(root)).replace('/etc/crontabs', str(root / 'etc/crontabs')).replace('/etc/init.d/cron restart', 'true').replace('/bin/coremark', '/bin/false')
            result = subprocess.run(['sh', '-c', body])
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(cron.read_text(), line)

    def test_xupnpd_ui_generation_and_syntax(self):
        import json
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            features.install_xupnpd(tree)
            package = tree / 'package/custom/luci-app-xupnpd'
            self.assertIn('+xupnpd +luci-base +rpcd-mod-file', (package / 'Makefile').read_text())
            root = package / 'root'
            subprocess.run(['node', '--check', str(root / 'www/luci-static/resources/view/aw1000/xupnpd.js')], check=True)
            acl = json.loads((root / 'usr/share/rpcd/acl.d/luci-app-xupnpd.json').read_text())
            self.assertEqual(acl['luci-app-xupnpd']['write']['file']['/etc/init.d/xupnpd restart'], ['exec'])
            self.assertNotIn('*', acl['luci-app-xupnpd']['write']['file'])

    def test_overlay_and_dependencies(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            features.install(tree)
            features.install(tree)
            files = tree / 'files'
            self.assertEqual(os.readlink(files / 'etc/openclash/core/clash_meta'), '/usr/libexec/mihomo')
            self.assertEqual(os.readlink(files / 'etc/rc.d/S99aw1000-buttons'), '../init.d/aw1000-buttons')
            self.assertIn('127.0.0.1:6053', (files / 'etc/mosdns/config.yaml').read_text())
            self.assertIn('wps_pushbutton=1', features.BOOT)
            for file in files.rglob('*'):
                if file.is_file() and file.read_text().startswith('#!/bin/sh'):
                    subprocess.run(['sh', '-n', str(file)], check=True)

    def test_coremark_feed_patch_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            tree = Path(folder)
            path = tree / 'feeds/packages/utils/coremark/Makefile'
            path.parent.mkdir(parents=True)
            path.write_text('define Package/coremark/postinst\n0 4 * * * /etc/coremark.sh\nendef\n\t$(INSTALL_BIN) ./coremark $(1)/etc/uci-defaults/xxx-coremark\n')
            features.prepare(tree, proxy_patches=False)
            first = path.read_text()
            features.prepare(tree, proxy_patches=False)
            self.assertEqual(first, path.read_text())
            self.assertNotIn('0 4', first)
            self.assertNotIn('$(INSTALL_BIN) ./coremark ', first)

    def test_proxy_owner_exclusion_and_teardown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'guard'
            script.write_text(features.PROXY_GUARD)
            tool = root / 'uci'
            tool.write_text('#!/bin/sh\nprintf "%s\\n" "${OC_ENABLED:-0}"\n')
            tool.chmod(0o755)
            env = {**os.environ, 'AW1000_PROXY_RUN': str(root / 'run'), 'PATH': str(root) + ':' + os.environ['PATH']}
            def run(action, name):
                return subprocess.run(['sh', str(script), action, name], env=env, capture_output=True).returncode
            self.assertEqual(run('start', 'shadowsocksr'), 0)
            self.assertNotEqual(run('start', 'openclash'), 0)
            self.assertNotEqual(run('stop', 'openclash'), 0)
            self.assertEqual((root / 'run/owner').read_text().strip(), 'shadowsocksr')
            self.assertEqual(run('stop', 'shadowsocksr'), 0)
            self.assertNotEqual(run('start', 'openclash'), 0)
            self.assertEqual(run('release', 'shadowsocksr'), 0)
            self.assertEqual(run('start', 'openclash'), 0)
            self.assertNotEqual(run('start', 'shadowsocksr'), 0)
            self.assertNotEqual(run('stop', 'shadowsocksr'), 0)

    def test_proxy_concurrent_start_has_one_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script = root / 'guard';script.write_text(features.PROXY_GUARD)
            tool = root / 'uci';tool.write_text('#!/bin/sh\necho 0\n');tool.chmod(0o755)
            env = {**os.environ, 'AW1000_PROXY_RUN': str(root / 'run'), 'PATH': str(root) + ':' + os.environ['PATH']}
            jobs = [subprocess.Popen(['sh', str(script), 'start', name], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for name in ('shadowsocksr', 'openclash')]
            for job in jobs: job.communicate()
            self.assertEqual(sorted(job.returncode for job in jobs), [0, 1])

    def test_openclash_enabled_blocks_ssr_after_reboot(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            script=root/'guard';script.write_text(features.PROXY_GUARD)
            tool=root/'uci';tool.write_text('#!/bin/sh\necho 1\n');tool.chmod(0o755)
            oc_init=root/'openclash';oc_init.write_text('#!/bin/sh\n');oc_init.chmod(0o755)
            env={**os.environ,'AW1000_PROXY_RUN':str(root/'run'),'AW1000_OPENCLASH_INIT':str(oc_init),'PATH':str(root)+':'+os.environ['PATH']}
            result=subprocess.run(['sh',str(script),'start','shadowsocksr'],env=env,capture_output=True)
            self.assertNotEqual(result.returncode,0)
            self.assertFalse((root/'run/owner').exists())
            oc_init.unlink()
            result=subprocess.run(['sh',str(script),'start','shadowsocksr'],env=env,capture_output=True)
            self.assertEqual(result.returncode,0)  # Old UCI alone must not block an uninstalled plugin.

    def test_patched_service_boundaries_block_dns_side_effects(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ssr=root/'feeds/helloworld/luci-app-ssr-plus/root/etc/init.d/shadowsocksr'
            oc=root/'feeds/luci/applications/luci-app-openclash/root/etc/init.d/openclash'
            ssr.parent.mkdir(parents=True);oc.parent.mkdir(parents=True)
            ssr.write_text('''start() {
echo ssr-start >> "$EVENTS"
}
stop() {
echo ssr-stop >> "$EVENTS"
\tunset_lock
}

reset() { :; }
''')
            oc.write_text('''start_service() {
   if procd_running "openclash" >/dev/null; then
       return 0
   fi
echo oc-start >> "$EVENTS"
}
stop_service()
{
echo oc-stop >> "$EVENTS"
   echo "OpenClash Already Stop!"
}
''')
            features.patch_proxy_services(root)
            first=ssr.read_text(),oc.read_text()
            features.patch_proxy_services(root)
            self.assertEqual(first,(ssr.read_text(),oc.read_text()))
            guard=root/'guard';guard.write_text(features.PROXY_GUARD);guard.chmod(0o755)
            uci=root/'uci';uci.write_text('#!/bin/sh\necho 0\n');uci.chmod(0o755)
            for file in (ssr,oc):file.write_text(file.read_text().replace('/usr/sbin/aw1000-proxy-guard',str(guard)))
            events=root/'events'
            env={**os.environ,'EVENTS':str(events),'AW1000_PROXY_RUN':str(root/'run'),'PATH':str(root)+':'+os.environ['PATH']}
            def call(file,fn):
                return subprocess.run(['sh','-c','unset_lock() { :; }; procd_running() { return 1; }; . "$1"; "$2"','sh',str(file),fn],env=env,capture_output=True).returncode
            self.assertEqual(call(ssr,'start'),0)
            self.assertNotEqual(call(oc,'start_service'),0)
            self.assertEqual(call(oc,'stop_service'),0)
            self.assertEqual(call(ssr,'stop'),0)
            self.assertEqual(call(oc,'start_service'),0)
            self.assertEqual(call(ssr,'stop'),0)
            self.assertEqual(events.read_text(),'ssr-start\nssr-stop\noc-start\n')

    def test_proxy_build_option_matrix(self):
        base = (ROOT / 'config/aw1000.config').read_text()
        for ssr in (False, True):
            for oc in (False, True):
                options={'include_ssr_plus':ssr,'include_openclash':oc}
                config=features.effective_config(base,options)
                self.assertEqual('CONFIG_PACKAGE_luci-app-ssr-plus=y' in config,ssr)
                self.assertEqual('CONFIG_PACKAGE_luci-app-openclash=y' in config,oc)
                self.assertEqual('CONFIG_PACKAGE_mihomo=y' in config,ssr or oc)
                with tempfile.TemporaryDirectory() as folder:
                    tree=Path(folder)
                    features.install(tree, options)
                    self.assertEqual((tree/'files/etc/openclash/core/clash_meta').is_symlink(),oc)
        with self.assertRaises(ValueError):
            features.proxy_options({'include_openclash':'false'})
