import json
import ast
import re
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import test_project as project
ROOT=project.ROOT
phone=project.load('phone')

class Phone(unittest.TestCase):
    setUp=project.Provisioning.setUp

    def configure_modem(self):
        state=json.loads(self.state.read_text())
        state.update({'qmodem.4_1':'modem-device','qmodem.4_1.name':'RG500Q-EA',
                      'qmodem.4_1.data_interface':'usb','qmodem.4_1.at_port':'/dev/ttyUSB3'})
        self.state.write_text(json.dumps(state))

    def test_actual_qmodem_at_port_and_ambiguous_modems(self):
        self.configure_modem()
        cmd=['sh',str(ROOT/'files/aw1000-phone-port')]
        p=subprocess.run(cmd,env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stdout.strip(),'/dev/ttyUSB3')
        state=json.loads(self.state.read_text())
        state.update({'qmodem.other':'modem-device','qmodem.other.name':'RG500Q-EA',
                      'qmodem.other.data_interface':'usb','qmodem.other.at_port':'/dev/ttyUSB7'})
        self.state.write_text(json.dumps(state))
        self.assertNotEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)

    def test_patch_and_packaging_include_driver_daemon_ui_and_no_led_plugin(self):
        self.assertEqual(phone.check_patch()['sha256'],'a8c305841adf327e180bff6cbac92ada5a0625ca2d9e8c22733e4b3f2b8cad99')
        phone.install(self.tree)
        custom=self.tree/'package/custom'
        self.assertFalse((custom/'luci-app-aw1k-led').exists())
        self.assertEqual((custom/'fxsd/files/si3226x_patch.bin').read_bytes(),(ROOT/'firmware/si3226x/si3226x_patch.bin').read_bytes())
        self.assertIn('$(TOPDIR)/aw1000-phone',(custom/'aw1000-fxs/Makefile').read_text())
        self.assertIn('$(KERNEL_MAKE)',(custom/'aw1000-fxs/Makefile').read_text())
        self.assertIn('aw1000-fxsd-run',(custom/'fxsd/files/fxsd.init').read_text())
        self.assertNotIn('green:phone',(custom/'fxsd/files/fxsd.init').read_text())
        self.assertIn("option led 'green:phone'",(custom/'fxsd/files/fxs.config').read_text())
        self.assertIn('echo none',(custom/'fxsd/files/fxsd.init').read_text())
        self.assertIn("option at_port 'auto'",(custom/'fxsd/files/fxs.config').read_text())
        self.assertIn('aw1000-phone-at',(self.tree/'aw1000-phone/fxsd/src/modem.c').read_text())
        self.assertTrue((self.tree/'files/etc/rc.d/S99fxsd').is_symlink())

    def test_simplified_chinese_covers_all_phone_views_and_menu_titles(self):
        phone.install(self.tree)
        ui=self.tree/'package/custom/luci-app-fxs'
        po=(ui/'files/fxs.zh-cn.po').read_text()
        entries={json.loads(a):json.loads(b) for a,b in re.findall(r'^msgid (".*")\nmsgstr (".*")',po,re.M)}
        required=set()
        for view in (ui/'files').glob('*.js'):
            for item in re.finditer(r"_\(\s*('(?:\\.|[^'\\])*')",view.read_text()):
                required.add(ast.literal_eval(item[1]))
        required.update(v['title'] for v in json.loads((ui/'files/menu.json').read_text()).values())
        self.assertFalse(required-set(entries))
        for key in required:
            self.assertTrue(entries[key],key)
            self.assertEqual(re.findall(r'%[sd]',key),re.findall(r'%[sd]',entries[key]),key)
        self.assertEqual(entries['Phone (RJ11)'],'电话（RJ11）')
        makefile=(ui/'Makefile').read_text()
        self.assertIn('PKG_BUILD_DEPENDS:=luci-base/host',makefile)
        self.assertIn('po2lmo ./files/fxs.zh-cn.po',makefile)
        self.assertIn('$(PKG_BUILD_DIR)/fxs.zh-cn.lmo $(1)/usr/lib/lua/luci/i18n/',makefile)

    def test_bad_patch_is_rejected(self):
        bad=self.path/'bad.bin';bad.write_bytes(b'FXSP'+b'\0'*904)
        with self.assertRaises(RuntimeError):phone.check_patch(bad)

    @unittest.skipUnless(shutil.which('gcc'),'gcc needed for real parser')
    def test_real_upstream_c_parser(self):
        src=phone.unpack_source(self.path/'upstream')/'fxsd/src';binary=self.path/'parser'
        subprocess.run(['gcc','-Wall','-Wextra','-Werror','-I',str(src),str(ROOT/'tests/phone_patch_harness.c'),str(src/'patch.c'),'-o',str(binary)],check=True)
        subprocess.run([str(binary),str(ROOT/'firmware/si3226x/si3226x_patch.bin')],check=True)

    def test_queue_response_failure_and_json_escaping(self):
        request=self.path/'request'
        ubus=self.path/'bin/ubus'
        ubus.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
assert sys.argv[1:6]==['-t','5','call','at-daemon','sendat']
Path(os.environ['REQUEST']).write_text(sys.argv[6])
print(json.dumps({'status':os.environ['STATUS'],'response':'+CLCC: 1,1,4,0,0,"13800000000",129\\r\\nOK'}))
''');ubus.chmod(0o755)
        self.env.update(REQUEST=str(request),STATUS='success')
        cmd=['sh',str(ROOT/'files/aw1000-phone-at'),'/dev/ttyUSB3','AT+CLCC']
        p=subprocess.run(cmd,env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertIn('+CLCC:',p.stdout)
        value=json.loads(request.read_text());self.assertEqual(value['owner'],'aw1000-phone')
        self.assertEqual(value['at_port'],'/dev/ttyUSB3')
        self.env['STATUS']='settings_pending'
        p=subprocess.run(cmd,env=self.env,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0);self.assertEqual(p.stdout,'')
        self.assertEqual(subprocess.run(cmd[:2]+['/dev/ttyUSB3;bad','AT'],env=self.env).returncode,2)

    def setup_script(self,mode='0',fail=''):
        self.env.update(PHONE_MODE=mode,PHONE_FAIL=fail,PHONE_LOG=str(self.path/'phone.log'))
        for name,text in {
            'phone-port':'#!/bin/sh\necho /dev/ttyUSB3\n',
            'phone-at':'''#!/bin/sh
printf '%s\\n' "$2" >> "$PHONE_LOG"
[ "$2" != "$PHONE_FAIL" ] || exit 1
if [ "$2" = 'AT+QDAI?' ]; then echo "+QDAI: 3,$PHONE_MODE,0,4,0,0,1,1"; fi
echo OK
''',
            'fxsd':'#!/bin/sh\nprintf "service %s\\n" "$*" >> "$PHONE_LOG"\n'}.items():
            p=self.path/'bin'/name;p.write_text(text);p.chmod(0o755)
        s=(ROOT/'files/aw1000-phone-setup').read_text().replace('/tmp/sysinfo/board_name',str(self.path/'board_name')).replace('/var/lock',str(self.path/'locks'))
        for a,b in [('aw1000-phone-port','phone-port'),('aw1000-phone-at','phone-at')]:s=s.replace('/usr/sbin/'+a,str(self.path/'bin'/b))
        return s.replace('/etc/init.d/fxsd',str(self.path/'bin/fxsd'))

    def test_explicit_pcm_setup_remains_available_and_is_idempotent(self):
        s=self.setup_script()
        p=subprocess.run(['sh','-c',s,'setup','configure'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual((self.path/'phone.log').read_text().splitlines(),['AT+QDAI?','AT+QDAI=x,1,0,4,0,0,1,1','AT+CFUN=1,1','service enable','service restart'])
        (self.path/'phone.log').unlink();self.env['PHONE_MODE']='1'
        p=subprocess.run(['sh','-c',s,'setup','configure'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual((self.path/'phone.log').read_text().splitlines(),['AT+QDAI?','service enable','service restart'])
        self.assertIn('phone-setup auto',(ROOT/'files/aw1000-fxsd-run').read_text())

    def test_pcm_failure_does_not_reboot_or_enable_service(self):
        s=self.setup_script(fail='AT+QDAI=x,1,0,4,0,0,1,1')
        p=subprocess.run(['sh','-c',s,'setup','configure'],env=self.env,capture_output=True,text=True)
        self.assertNotEqual(p.returncode,0)
        log=(self.path/'phone.log').read_text();self.assertNotIn('AT+CFUN',log);self.assertNotIn('service',log)

    def test_automatic_setup_runs_once_without_recursive_service_restart(self):
        s=self.setup_script()
        cmd=['sh','-c',s,'setup','auto']
        p=subprocess.run(cmd,env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual((self.path/'phone.log').read_text().splitlines(),['AT+QDAI?','AT+QDAI=x,1,0,4,0,0,1,1','AT+CFUN=1,1'])
        self.assertEqual(json.loads(self.state.read_text())['fxs.main.pcm_auto_state'],'done')
        before=(self.path/'phone.log').read_text()
        self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
        self.assertEqual((self.path/'phone.log').read_text(),before)

    def test_automatic_failure_persists_attempt_and_never_loops_reboot(self):
        s=self.setup_script(fail='AT+CFUN=1,1');cmd=['sh','-c',s,'setup','auto']
        self.assertNotEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
        self.assertEqual(json.loads(self.state.read_text())['fxs.main.pcm_auto_state'],'attempted')
        before=(self.path/'phone.log').read_text()
        self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
        self.assertEqual((self.path/'phone.log').read_text(),before)

    def test_automatic_slave_mode_never_restarts_modem(self):
        s=self.setup_script(mode='1')
        p=subprocess.run(['sh','-c',s,'setup','auto'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual((self.path/'phone.log').read_text().splitlines(),['AT+QDAI?'])
        self.assertEqual(json.loads(self.state.read_text())['fxs.main.pcm_auto_state'],'done')

    def test_modem_not_ready_can_retry_without_consuming_attempt(self):
        s=self.setup_script(fail='AT+QDAI?');cmd=['sh','-c',s,'setup','auto']
        self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,75)
        self.assertNotIn('fxs.main.pcm_auto_state',json.loads(self.state.read_text()))
        self.env['PHONE_FAIL']=''
        self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
        self.assertEqual((self.path/'phone.log').read_text().count('AT+CFUN=1,1'),1)

    def test_auto_opt_out_and_manual_port_preserved(self):
        s=self.setup_script();cmd=['sh','-c',s,'setup','auto']
        for key,value in [('fxs.main.auto_pcm','0'),('fxs.main.at_port','/dev/ttyUSB9')]:
            self.state.write_text(json.dumps({key:value}))
            self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
            self.assertFalse((self.path/'phone.log').exists())

    def test_boot_wrapper_waits_for_setup_then_executes_daemon(self):
        log=self.path/'boot.log';attempt=self.path/'attempt'
        self.env.update(BOOT_LOG=str(log),BOOT_ATTEMPT=str(attempt))
        mocks={
            'port':'#!/bin/sh\necho /dev/ttyUSB3\n',
            'setup':'''#!/bin/sh
printf 'setup %s\n' "$*" >> "$BOOT_LOG"
if [ ! -f "$BOOT_ATTEMPT" ]; then touch "$BOOT_ATTEMPT"; exit 75; fi
''',
            'sleep':'#!/bin/sh\necho wait >> "$BOOT_LOG"\n',
            'daemon':'#!/bin/sh\nprintf "daemon %s\\n" "$*" >> "$BOOT_LOG"\n'}
        for name,text in mocks.items():
            p=self.path/'bin'/name;p.write_text(text);p.chmod(0o755)
        s=(ROOT/'files/aw1000-fxsd-run').read_text().replace('[ -c "$port" ]','true')
        for old,new in [('aw1000-phone-port','port'),('aw1000-phone-setup','setup'),('fxsd','daemon')]:
            s=s.replace('/usr/sbin/'+old,str(self.path/'bin'/new))
        p=subprocess.run(['sh','-c',s,'wrapper','auto','-p','/etc/fxs/si3226x_patch.bin'],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(log.read_text().splitlines(),['setup auto','wait','setup auto','daemon -a /dev/ttyUSB3 -p /etc/fxs/si3226x_patch.bin'])

    def test_browser_upload_may_omit_git_metadata_but_not_build_sources(self):
        from unittest.mock import patch
        local=self.path/'project'
        shutil.copytree(ROOT/'vendor',local/'vendor')
        shutil.copytree(ROOT/'firmware',local/'firmware')
        shutil.copytree(ROOT/'files',local/'files')
        shutil.copyfile(ROOT/'sources.lock.json',local/'sources.lock.json')
        vendor=phone.unpack_source(self.path/'unpacked-source')
        (vendor/'.gitignore').unlink()
        with patch.object(phone,'ROOT',local):
            phone.install_source(self.path/'browser-upload-tree',vendor)
            (vendor/'fxsd/src/modem.c').unlink()
            with self.assertRaisesRegex(RuntimeError,'fxsd/src/modem.c'):
                phone.install_source(self.path/'missing-source-tree',vendor)

    def test_present_git_metadata_and_build_sources_still_require_matching_hashes(self):
        from unittest.mock import patch
        local=self.path/'project'
        shutil.copytree(ROOT/'vendor',local/'vendor')
        shutil.copyfile(ROOT/'sources.lock.json',local/'sources.lock.json')
        vendor=phone.unpack_source(self.path/'unpacked-source')
        with patch.object(phone,'ROOT',local):
            (vendor/'fxsd/src/modem.c').write_text('changed')
            with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                phone.install_source(self.path/'changed-tree',vendor)

    def test_corrupt_source_archive_is_rejected_before_extraction(self):
        from unittest.mock import patch
        local=self.path/'project';(local/'vendor').mkdir(parents=True)
        shutil.copyfile(ROOT/'sources.lock.json',local/'sources.lock.json')
        (local/'vendor/openwrt-aw1000-phone.tar.xz').write_bytes(b'corrupt')
        with patch.object(phone,'ROOT',local):
            with self.assertRaisesRegex(RuntimeError,'archive checksum mismatch'):
                phone.unpack_source(self.path/'rejected-source')
        self.assertFalse((self.path/'rejected-source').exists())

    def test_upload_project_has_at_most_100_files(self):
        excluded={'.git','.private','work','output','__pycache__','.pytest_cache'}
        files=[p for p in ROOT.rglob('*') if p.is_file() and p.suffix!='.pyc' and
               not any(part in excluded for part in p.relative_to(ROOT).parts)]
        self.assertLessEqual(len(files),100)
