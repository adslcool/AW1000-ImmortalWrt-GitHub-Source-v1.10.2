import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

import test_project as project
board_state = project.load('board_state')
ROOT = project.ROOT


class BuildIntegrationTests(unittest.TestCase):
    def test_embedded_defaults_and_secret_override(self):
        value, admin = project.settings.validate({})
        self.assertTrue(value['include_device_init'])
        self.assertEqual(value['lan_ip'], '192.168.1.1')
        self.assertEqual(admin, '')
        self.assertEqual(value['wifi_password'], '1234567890')
        self.assertEqual(value['board_state_key'], board_state.DEFAULT_KEY_HEX)
        secret = bytes(range(32)).hex()
        value, admin = project.settings.validate(project.valid_env(INCLUDE_DEVICE_INIT='true', BOARD_STATE_KEY=secret, ADMIN_PASSWORD=''))
        self.assertEqual(value['board_state_key'], secret)
        self.assertEqual(admin, '')
        with self.assertRaises(ValueError):
            project.settings.validate(project.valid_env(INCLUDE_DEVICE_INIT='true', BOARD_STATE_KEY='bad', ADMIN_PASSWORD=''))

    def test_blank_inputs_fall_back_with_or_without_component(self):
        for selected in ('true', 'false'):
            value, admin = project.settings.validate(dict(INCLUDE_DEVICE_INIT=selected,
                BOARD_STATE_KEY='', ADMIN_PASSWORD='', WIFI_PASSWORD='', LAN_IP=''))
            self.assertEqual(admin, '')
            self.assertEqual(value['wifi_password'], '1234567890')
            self.assertEqual(value['lan_ip'], '192.168.1.1')
            self.assertTrue(value['include_device_init'])
            self.assertEqual(value['board_state_key'], board_state.DEFAULT_KEY_HEX)
        workflow = (ROOT/'.github/workflows/build-aw1000.yml').read_text()
        self.assertIn('inputs.admin_password || secrets.ROUTER_ADMIN_PASSWORD', workflow)
        self.assertIn('inputs.wifi_password || secrets.WIFI_PASSWORD', workflow)
        self.assertNotRegex(workflow, r'(admin_password|wifi_password):[\s\S]{0,150}default: password')

    def test_main_password_source_controls_component_and_hash(self):
        from unittest.mock import patch
        for admin in ('', 'CustomPassword42'):
            with self.subTest(custom=bool(admin)), tempfile.TemporaryDirectory() as d:
                with patch.object(project.settings, 'ROOT', Path(d)), patch.dict(os.environ,
                        {'ADMIN_PASSWORD': admin}, clear=True):
                    project.settings.main()
                value = json.loads((Path(d)/'.private/settings.json').read_text())
                self.assertEqual(value['include_device_init'], not bool(admin))
                hashed = value['root_password_hash']
                tree = Path(d)/'build'
                board_state.install(tree, value)
                self.assertEqual((tree/'package/custom/aw1000-board-state').exists(), not bool(admin))
                if not admin:
                    self.assertEqual(hashed, '!')
                    continue
                self.assertEqual(value['board_state_key'], '')
                self.assertTrue(hashed.startswith('$6$'))
                check = subprocess.run(['openssl', 'passwd', '-6', '-salt', hashed.split('$')[2], '-stdin'],
                    input=admin+'\n', text=True, capture_output=True, check=True)
                self.assertEqual(hashed, check.stdout.strip())

    def test_explicit_password_excludes_generator_even_with_legacy_flag(self):
        value, admin = project.settings.validate(project.valid_env(INCLUDE_DEVICE_INIT='true', BOARD_STATE_KEY='bad'))
        self.assertFalse(value['include_device_init'])
        self.assertEqual(value['board_state_key'], '')
        self.assertEqual(admin, project.valid_env()['ADMIN_PASSWORD'])

    def test_optional_package_and_secret_are_private(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            secret = bytes(range(32)).hex()
            values, _ = project.settings.validate(project.valid_env(INCLUDE_DEVICE_INIT='true', BOARD_STATE_KEY=secret, ADMIN_PASSWORD=''))
            values['root_password_hash'] = '!'
            project.configure.overlay(tree, values)
            package = tree/'package/custom/aw1000-board-state'
            self.assertTrue((package/'src/board_key.h').is_file())
            self.assertEqual((package/'src/board_key.h').stat().st_mode & 0o777, 0o600)
            self.assertIn('-static', (package/'Makefile').read_text())
            self.assertIn('+coreutils-timeout', (package/'Makefile').read_text())
            conf = (tree/'files/etc/aw1000-build.conf').read_text()
            self.assertIn('DEVICE_INIT_ENABLED=1', conf)
            self.assertNotIn(secret, conf)
            self.assertNotIn('board_state_key', conf)
            board_state.install(tree, {'include_device_init': False})
            self.assertFalse(package.exists())
            self.assertFalse((tree/'files/etc/rc.d/S99aw1000-board-state').is_symlink())

    def test_unselected_config_does_not_leave_enabled_package(self):
        text = board_state.config('CONFIG_PACKAGE_aw1000-board-state=y\n', False)
        self.assertNotIn('CONFIG_PACKAGE_aw1000-board-state=y', text)
        self.assertIn('# CONFIG_PACKAGE_aw1000-board-state is not set', text)

    def test_runtime_gate_requires_static_aarch64(self):
        import struct
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for name in board_state.RUNTIME_FILES:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'x')
            elf=bytearray(120);elf[:6]=b'\x7fELF\x02\x01'
            struct.pack_into('<H',elf,18,183)
            struct.pack_into('<Q',elf,32,64)
            struct.pack_into('<HH',elf,54,56,1)
            struct.pack_into('<I',elf,64,1)
            binary=root/board_state.BINARY;binary.write_bytes(elf)
            board_state.validate_root(root,True)
            with self.assertRaises(RuntimeError):board_state.validate_root(root,False)
            struct.pack_into('<I',elf,64,3);binary.write_bytes(elf)
            with self.assertRaises(RuntimeError):board_state.validate_root(root,True)
            struct.pack_into('<H',elf,18,62);binary.write_bytes(elf)
            with self.assertRaises(RuntimeError):board_state.validate_root(root,True)

    def test_runner_preserves_upgrade_and_cleans_verified_initialization(self):
        for preserved, failure in [(True, False), (False, False), (False, True)]:
            with self.subTest(preserved=preserved, failure=failure), tempfile.TemporaryDirectory() as d:
                base = Path(d)
                files = base/'root'; files.mkdir()
                state = files/'etc/aw1000-board-state'; state.mkdir(parents=True)
                provisioned = files/'etc/aw1000-provisioned'; provisioned.touch()
                folder = files/'usr/libexec/aw1000-board'; folder.mkdir(parents=True)
                service = files/'etc/init.d/aw1000-board-state'; service.parent.mkdir(parents=True)
                service.write_text('#!/bin/sh\nexit 0\n'); service.chmod(0o700)
                program = folder/'stage1'
                called = base/'called'
                program.write_text('#!/bin/sh\ntouch "'+str(called)+'"\n'+('exit 2\n' if failure else 'touch "'+str(state/'ready')+'"\n'))
                program.chmod(0o700)
                if not preserved: (state/'requested').touch()
                text = board_state.RUNNER.replace('/etc/', str(files/'etc')+'/').replace('/usr/libexec/', str(files/'usr/libexec')+'/')
                runner = folder/'runner'; runner.write_text(text)
                result = subprocess.run(['sh', str(runner)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2 if failure else 0, result.stderr)
                self.assertEqual(called.exists(), not preserved)
                self.assertEqual(program.exists(), failure)
                self.assertEqual(runner.exists(), failure)
                self.assertEqual(service.exists(), failure)

    def test_workflow_default_and_fixed_country(self):
        workflow = (ROOT/'.github/workflows/build-aw1000.yml').read_text()
        self.assertNotIn('      include_device_init:', workflow)
        self.assertIn("default: '1234567890'", workflow)
        self.assertIn('secrets.BOARD_STATE_KEY', workflow)
        self.assertNotIn('inputs.country_code', workflow)
        self.assertNotIn('      country_code:', workflow)

def derive(imei, key):
    import hmac
    alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
    for counter in range(100):
        digest = hmac.digest(key, f'aw1000:root-password:v2:{imei}:{counter}'.encode(), 'sha256')
        p = ''.join(alphabet[b % 36] for b in digest if b < 252)[:8]
        if len(p) == 8 and re.search('[a-z]', p) and re.search('[0-9]', p):
            return p
    raise AssertionError('derivation failed')


class ProgramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.build.cleanup)
        cls.build_dir = Path(cls.build.name)
        cls.key = bytes(range(32))
        a = bytes([0x5a] * 32)
        b = bytes(x ^ y for x, y in zip(a, cls.key))
        header = ''.join('static const unsigned char ' + name + '[32] = {' + ','.join(str(x) for x in data) + '};\n'
                         for name, data in [('key_a', a), ('key_b', b)])
        (cls.build_dir / 'board_key.h').write_text(header + 'static const char key_id[]="public-test-key";\n')
        (cls.build_dir / 'password.c').write_bytes(board_state.C_SOURCE.encode())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / 'bin'; self.bin.mkdir()
        self.state = self.base / 'state'
        self.capture = self.base / 'capture'
        self.imei = '869119050618808'
        self.password = derive(self.imei, self.key)
        self.exe = self.base / 'program'
        args = [os.environ.get('AW_TEST_CC', 'cc'), '-std=c99', '-Os', '-Wall', '-Wextra', '-Werror',
                '-DAW_TEST', f'-DSTATE_DIR="{self.state}"', str(self.build_dir / 'password.c')]
        crypto = os.environ.get('AW_TEST_CRYPTO_DIR')
        if crypto:
            args += ['-I', str(Path(crypto) / 'include'), str(Path(crypto) / 'libcrypto.a'), '-static']
        else:
            args += ['-lcrypto']
        args += ['-lcrypt', '-o', str(self.exe)]
        subprocess.run(args, check=True)
        self.prefix = [os.environ['AW_TEST_QEMU']] if os.environ.get('AW_TEST_QEMU') else []
        self.mock('uci', 'echo /dev/ttyUSB3')
        self.mock('tom_modem', 'printf "%s\\nOK\\n" "$TEST_IMEI"')
        self.mock('passwd', '/bin/cat >> "$CAPTURE"; printf "%s\\n" "$TEST_HASH" > "$TEST_SHADOW"')
        (self.bin / 'timeout').symlink_to(shutil.which('timeout'))
        hashed = subprocess.check_output(['openssl', 'passwd', '-6', '-stdin'], input=self.password+'\n', text=True).strip()
        self.env = dict(os.environ, PATH=str(self.bin), TEST_IMEI=self.imei, CAPTURE=str(self.capture),
                        TEST_HASH=hashed, TEST_SHADOW=str(self.state / 'test-shadow'))

    def mock(self, name, body):
        path = self.bin / name; path.write_text('#!/bin/sh\n'+body+'\n'); path.chmod(0o700)

    def run_program(self, *args):
        return subprocess.run(self.prefix + [str(self.exe), *args], env=self.env, text=True, capture_output=True)

    def test_cross_language_vectors(self):
        for i in range(100):
            imei = str(int(self.imei) + i)
            r = self.run_program('--test-imei', imei)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), derive(imei, self.key))

    def test_once_and_shadow_verification(self):
        r = self.run_program('--init')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.state / 'ready').is_file())
        first = self.capture.read_bytes()
        self.assertEqual(first.decode(), self.password+'\n'+self.password+'\n')
        self.mock('passwd', 'exit 8')
        # Ordinary re-runs must also preserve a user's later custom password.
        (self.state / 'test-shadow').write_text('!user-changed-password\n')
        self.assertEqual(self.run_program('--init').returncode, 0)
        self.assertEqual(self.capture.read_bytes(), first)
        self.assertNotIn(self.password, (self.state / 'ready').read_text())

    def test_false_success_does_not_initialize(self):
        self.mock('passwd', '/bin/cat >> "$CAPTURE"; exit 0')
        self.assertEqual(self.run_program('--init').returncode, 2)
        self.assertFalse((self.state / 'ready').exists())
        self.assertTrue((self.state / 'pending').exists())
        first = self.capture.read_bytes()
        self.assertEqual(self.run_program('--init').returncode, 2)
        self.assertEqual(self.capture.read_bytes(), first)

    def test_at_error_does_not_change_password(self):
        self.mock('tom_modem', 'echo "+CME ERROR: 10"')
        self.assertNotEqual(self.run_program('--init').returncode, 0)
        self.assertFalse(self.capture.exists())
        self.assertFalse((self.state / 'pending').exists())

    def test_passwd_failure_keeps_guard(self):
        self.mock('passwd', '/bin/cat >> "$CAPTURE"; exit 1')
        self.assertEqual(self.run_program('--init').returncode, 2)
        self.assertTrue((self.state / 'pending').is_file())
        self.assertFalse((self.state / 'ready').exists())

    def legacy_record(self):
        self.state.mkdir()
        text = f'algorithm=v2\nimei={self.imei}\n'
        (self.state / 'ready').write_text(text)
        return text

    def test_legacy_marker_needs_explicit_repair(self):
        self.legacy_record()
        self.assertEqual(self.run_program('--init').returncode, 3)
        self.assertFalse(self.capture.exists())
        result = self.run_program('--repair')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.capture.read_text(), self.password+'\n'+self.password+'\n')
        self.assertIn('verification=crypt-v1', (self.state / 'ready').read_text())
        self.assertEqual(self.run_program('--verify-current').returncode, 0)

    def test_repair_matching_password_does_not_reset(self):
        self.legacy_record()
        (self.state / 'test-shadow').write_text(self.env['TEST_HASH']+'\n')
        self.mock('passwd', 'exit 9')
        self.assertEqual(self.run_program('--repair').returncode, 0)
        self.assertFalse(self.capture.exists())
        self.assertIn('verification=crypt-v1', (self.state / 'ready').read_text())

    def test_failed_repair_keeps_legacy_record_and_blocks_cleanup(self):
        old = self.legacy_record()
        self.mock('passwd', '/bin/cat >> "$CAPTURE"; exit 1')
        self.assertEqual(self.run_program('--repair').returncode, 2)
        self.assertEqual((self.state / 'ready').read_text(), old)
        self.assertTrue((self.state / 'pending').exists())
        self.assertEqual(self.run_program('--init').returncode, 2)

    def test_verify_current_is_read_only(self):
        self.state.mkdir()
        self.assertEqual(self.run_program('--verify-current').returncode, 3)
        (self.state / 'test-shadow').write_text(self.env['TEST_HASH']+'\n')
        self.assertEqual(self.run_program('--verify-current').returncode, 0)
        self.assertFalse((self.state / 'ready').exists())
        self.assertFalse((self.state / 'pending').exists())
        self.assertFalse(self.capture.exists())


