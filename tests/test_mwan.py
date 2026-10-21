import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_project as project
ROOT=project.ROOT
mwan=project.load('mwan')

class Mwan(unittest.TestCase):
    setUp=project.Provisioning.setUp
    def test_pinned_packages_are_native_nft_and_have_correct_dependencies(self):
        mwan.install(self.tree)
        package=self.tree/'package/custom/mwan3'
        text=(package/'Makefile').read_text()
        self.assertIn('PKG_VERSION:=3.6.12',text)
        self.assertIn('PKG_BUILD_DEPENDS:=libnetfilter-conntrack libmnl',text)
        self.assertNotIn('+iptables',text)
        self.assertTrue((package/'files/lib/mwan3/mwan3-skeleton.nft').is_file())
        self.assertIn('include $(TOPDIR)/feeds/luci/luci.mk',(self.tree/'package/custom/luci-app-mwan3/Makefile').read_text())

    def fixture(self):
        data={'mwan3.globals.aw1000_enabled':'1','mwan3.globals.mmx_mask':'0x00ff0000',
              'mwan3.globals.aw1000_enabled':'1','mwan3.globals.aw1000_cellular':'wwan0','mwan3.globals.aw1000_modem':'4_1',
              'mwan3.wan':'interface','mwan3.wwan0':'interface'}
        self.state.write_text(json.dumps(data))
        self.log=self.path/'follow.log'
        commands={
            'nft':'''#!/bin/sh
case "$*" in '-nn list chain inet mwan3 mwan3_policy_aw_failover') :;; *) exit 4;; esac
[ "$MWAN_MARK" != missing ] || exit 1
printf 'meta mark & 0xff0000 == 0 meta mark set meta mark & 0xff00ffff %s %s\n' "${MWAN_OP:-|}" "$MWAN_MARK"
''',
            'follow':'''#!/usr/bin/env python3
import os,sys,json
from pathlib import Path
Path(os.environ['FOLLOW_LOG']).write_text(json.dumps(sys.argv[1:]))
'''}
        for name,text in commands.items():
            p=self.path/'bin'/name;p.write_text(text);p.chmod(0o755)
        self.env['FOLLOW_LOG']=str(self.log)
        self.env['AW1000_MWAN_RUN']=str(self.path/'run')
        script=(ROOT/'files/aw1000-mwan-follow.sh').read_text().replace('/usr/sbin/aw1000-ipv6-follow',str(self.path/'bin/follow')).replace('/etc/init.d/dnsmasq',str(self.path/'bin/logger')).replace('/usr/sbin/aw1000-leds', str(self.path/'bin/logger'))
        self.script=self.path/'observer';self.script.write_text(script)

    def test_observer_follows_published_policy_wired_cellular_none(self):
        self.fixture()
        for mark,expected in [('0x10000','wan'),('0x20000','wwan0'),('0xfd0000',''),('missing','')]:
            self.env['MWAN_MARK']=mark
            p=subprocess.run(['sh',str(self.script),'--once'],env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            args=json.loads(self.log.read_text());self.assertEqual(args[0],expected)
            for op in ('xor','^'):
                self.env['MWAN_OP']=op
                p=subprocess.run(['sh',str(self.script),'--once'],env=self.env,capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertEqual(json.loads(self.log.read_text())[0],expected)
            self.env.pop('MWAN_OP',None)
            self.assertEqual([x['target'] for x in json.loads(args[1])],['wan','wwan0'])

    def test_observer_refuses_unexpected_mask(self):
        self.fixture();self.env['MWAN_MARK']='0x10000'
        data=json.loads(self.state.read_text());data['mwan3.globals.mmx_mask']='0x3f00';self.state.write_text(json.dumps(data))
        self.assertNotEqual(subprocess.run(['sh',str(self.script),'--once'],env=self.env).returncode,0)
        self.assertFalse(self.log.exists())

    def test_recovery_uses_fresh_mwan_status_not_a_second_ping(self):
        self.fixture();track=self.path/'tracks/wwan0';track.mkdir(parents=True)
        (track/'PID').write_text(str(os.getpid()))
        (track/'TIME').write_text(str(int(float(Path('/proc/uptime').read_text().split()[0]))))
        self.env['AW1000_MWAN_TRACK']=str(track.parent)
        cmd=['sh',str(ROOT/'files/aw1000-mwan-health.sh'),'4_1']
        for status,expected in [('online',0),('disconnecting',0),('offline',1),('connecting',3)]:
            (track/'STATUS').write_text(status)
            self.assertEqual(subprocess.run(cmd,env=self.env).returncode,expected)
        (track/'TIME').write_text('0')
        self.assertEqual(subprocess.run(cmd,env=self.env).returncode,3)
        self.assertEqual(subprocess.run(cmd[:-1]+['other'],env=self.env).returncode,2)

    def test_native_policy_cannot_reselect_sim_blocked_member(self):
        mwan.install(self.tree)
        text=(self.tree/'package/custom/mwan3/files/lib/mwan3/mwan3.sh').read_text()
        start=text.index('mwan3_set_policy()\n{')
        function=text[start:text.index('\n}\n',start)+3]
        blocked=self.path/'blocked';blocked.write_text('wwan0\n')
        function=function.replace('/var/run/aw1000-internet/blocked',str(blocked))
        script=self.path/'policy-gate'
        script.write_text(function+"""
config_get() { case "$2:$3" in member:interface) eval "$1=wwan0";; member:metric|member:weight) eval "$1=1";; esac; }
mwan3_get_iface_id() { echo unexpected >> "$POLICY_CALLS"; }
DEFAULT_LOWEST_METRIC=256
mwan3_set_policy member
""")
        calls=self.path/'calls'
        env={**self.env,'POLICY_CALLS':str(calls)}
        subprocess.run(['sh',str(script)],env=env,check=True)
        self.assertFalse(calls.exists())
        blocked.write_text('other\n')
        subprocess.run(['sh',str(script)],env=env,check=True)
        self.assertEqual(calls.read_text().strip(),'unexpected')

        tracker=(self.tree/'package/custom/mwan3/files/usr/sbin/mwan3track').read_text()
        begin=tracker.index('firstconnect() {')
        function=tracker[begin:tracker.index('\n}\n',begin)+3].replace('/var/run/aw1000-internet/blocked',str(blocked))
        script.write_text(function+"""
network_flush_cache() { :; }
mwan3_get_true_iface() { eval "$1=wwan0"; }
network_get_device() { eval "$1=wwan0"; }
network_is_up() { return 0; }
mwan3_get_src_ip() { :; }
LOG() { :; }
started() { :; }
connected() { echo online; }
disconnected() { echo offline; }
INTERFACE=wwan0
STATUS=online
firstconnect
""")
        blocked.write_text('wwan0\n')
        result=subprocess.run(['sh',str(script)],env=env,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'offline')
        blocked.write_text('other\n')
        result=subprocess.run(['sh',str(script)],env=env,capture_output=True,text=True,check=True)
        self.assertEqual(result.stdout.strip(),'online')

    def test_default_configuration_is_ipv4_failover_with_domestic_targets(self):
        text=(ROOT/'files/mwan3.config').read_text()
        self.assertIn("option mmx_mask '0x00ff0000'",text)
        self.assertIn("option up '15'",text);self.assertIn("option down '2'",text)
        self.assertIn('223.5.5.5',text);self.assertIn('119.29.29.29',text)
        self.assertNotIn("family 'ipv6'",text)
        self.assertIn("option last_resort 'unreachable'",text)

    def test_dns_follows_selected_line_and_cleanup_removes_only_own_fragment(self):
        self.fixture()
        with tempfile.TemporaryDirectory(prefix='dnsmasq.aw1000-',suffix='.d') as directory:
            data=json.loads(self.state.read_text())
            data.update({'dhcp.main':'dnsmasq','dhcp.main.confdir':directory})
            self.state.write_text(json.dumps(data))
            p=self.path/'bin/ubus'
            p.write_text('''#!/usr/bin/env python3
import json,sys
iface=sys.argv[2].removeprefix('network.interface.')
servers={'wan':'192.168.1.1','wwan0':'183.203.33.222','wan6':'2408::53','wwan0v6':'2409::53'}
if iface not in servers:sys.exit(1)
print(json.dumps({'up':True,'dns-server':[servers[iface]]}))
''');p.chmod(0o755)
            own=Path(directory)/'aw1000-mwan.conf'
            custom=Path(directory)/'custom.conf';custom.write_text('server=/example.com/1.1.1.1\n')
            for mark,server,absent,v6,other_v6 in [('0x10000','192.168.1.1','183.203.33.222','2408::53','2409::53'),('0x20000','183.203.33.222','192.168.1.1','2409::53','2408::53')]:
                self.env['MWAN_MARK']=mark
                p=subprocess.run(['sh',str(self.script),'--once'],env=self.env,capture_output=True,text=True)
                self.assertEqual(p.returncode,0,p.stderr)
                self.assertIn('server='+server,own.read_text());self.assertNotIn(absent,own.read_text())
                self.assertIn('server='+v6,own.read_text());self.assertNotIn(other_v6,own.read_text())
            self.assertEqual(subprocess.run(['sh',str(self.script),'--restore'],env=self.env,capture_output=True).returncode,0)
            self.assertFalse(own.exists());self.assertTrue(custom.exists())


class Internet(unittest.TestCase):
    setUp=project.Provisioning.setUp

    def fixture(self):
        internet=project.load('internet')
        self.run=self.path/'internet-run'
        self.pending=self.path/'pending'
        self.config=self.path/'config';self.config.mkdir()
        sysroot=self.path/'sys';usb=sysroot/'devices/usb/modem'
        usb.mkdir(parents=True);(usb/'busnum').write_text('4');(usb/'devnum').write_text('2')
        (usb/'iface').mkdir();tty=sysroot/'class/tty/ttyUSB3';tty.mkdir(parents=True)
        (tty/'device').symlink_to(usb/'iface',target_is_directory=True)
        self.env['AW1000_SYS_ROOT']=str(sysroot)
        for name in ('network','mwan3','aw1000','qmodem'):
            (self.config/name).write_text('original-'+name)
        self.env.update(AW1000_INTERNET_RUN=str(self.run),AW1000_PENDING=str(self.pending),AW1000_CONFIG_ROOT=str(self.config))
        mock=self.path/'bin/uci'
        mock.write_text(mock.read_text().replace('elif cmd != "commit":','elif cmd == "changes": pass\nelif cmd != "commit":'))
        self.state.write_text(json.dumps({'mwan3.globals.aw1000_enabled':'1','mwan3.globals.aw1000_cellular':'wwan0','mwan3.globals.aw1000_modem':'4_1',
            'mwan3.aw_default':'rule','mwan3.aw_default.use_policy':'aw_failover','mwan3.aw_default.family':'ipv4',
            'mwan3.aw_default.dest_ip':'0.0.0.0/0','mwan3.aw_failover.last_resort':'unreachable',
            'mwan3.aw_failover.use_member':['aw_wired','aw_cell'],'mwan3.aw_wired.interface':'wan',
            'qmodem.4_1.at_port':'/dev/ttyUSB3','network.wan.device':'wan','network.wan.proto':'dhcp','network.lan.ipaddr':'192.168.31.1'}))
        self.actions=self.path/'actions'
        self.env['ACTIONS']=str(self.actions)
        mwan=self.path/'bin/mwan'
        mwan.write_text('#!/bin/sh\necho "$*" >> "$ACTIONS"\n');mwan.chmod(0o755)
        ubus=self.path/'bin/ubus'
        ubus.write_text('#!/bin/sh\necho \'{"up":true}\'\n');ubus.chmod(0o755)
        self.internet=self.path/'internet'
        self.internet.write_text(internet.RUNTIME.replace('/usr/sbin/mwan3',str(mwan)).replace('/etc/init.d/mwan3',str(mwan)))

    def invoke(self,*args,input=''):
        result=subprocess.run(['sh',str(self.internet),*args],input=input,env=self.env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def request(self,**overrides):
        args={'priority':'cellular','mode':'pppoe','username':"user'$()",'password':"secret'$(touch SHOULD_NOT_EXIST)",'vlan':'41','mtu':'1492'}
        args.update(overrides)
        return json.loads(self.invoke('--rpc','call','apply',input=json.dumps(args)))

    def test_explicit_sim_results_and_unknown_are_distinct(self):
        self.fixture()
        for response,state in [('AT+CPIN?\r\n+CPIN: READY\r\nOK','ready'),('+CME ERROR: 10\r\n','absent'),
                ('+CME ERROR: 100','unknown'),('+CPIN: SIM PIN','locked'),('+CME ERROR: 12','locked'),
                ('+CME ERROR: 13','failed'),('ERROR','unknown'),('+CME ERROR: 14','unknown'),('','unknown')]:
            with self.subTest(response=response):
                self.assertEqual(self.invoke('--classify',input=response).strip(),state)

    def test_absent_blocks_once_unknown_cannot_release_and_ready_recovers(self):
        self.fixture()
        self.invoke('--sim-result','4_1',input='+CME ERROR: 10')
        self.invoke('--tick');self.invoke('--tick')
        self.assertEqual((self.run/'blocked').read_text().strip(),'wwan0')
        self.assertEqual(self.actions.read_text().splitlines(),['ifdown wwan0'])
        self.invoke('--sim-result','4_1',input='ERROR');self.invoke('--tick')
        self.assertTrue((self.run/'blocked').exists())
        self.invoke('--sim-result','4_1',input='+CPIN: READY');self.invoke('--tick')
        self.assertTrue((self.run/'blocked').exists())
        track=self.path/'tracks/wwan0';track.mkdir(parents=True)
        self.env['AW1000_MWAN_TRACK']=str(track.parent)
        (track/'STATUS').write_text('connecting');self.invoke('--tick')
        (track/'STATUS').write_text('online')
        (track/'TIME').write_text(Path('/proc/uptime').read_text().split('.')[0])
        self.invoke('--tick')
        self.assertFalse((self.run/'blocked').exists())
        self.assertEqual(self.actions.read_text().splitlines(),['ifdown wwan0','ifup wwan0','ifup wwan0'])

    def test_freshness_modem_identity_and_unknown_do_not_withdraw_working_line(self):
        self.fixture()
        self.invoke('--sim-result','other',input='+CME ERROR: 10');self.invoke('--tick')
        self.assertFalse((self.run/'blocked').exists())
        self.invoke('--sim-result','4_1',input='+CPIN: READY')
        config=json.loads(self.state.read_text());config.pop('mwan3.globals.aw1000_modem');self.state.write_text(json.dumps(config))
        self.assertEqual(self.invoke('--sim-state','4_1').strip(),'ready')
        self.assertEqual(self.invoke('--sim-state','other').strip(),'unknown')
        config['mwan3.globals.aw1000_modem']='4_1';self.state.write_text(json.dumps(config))
        data=json.loads((self.run/'sim.json').read_text());data['time']=0
        (self.run/'sim.json').write_text(json.dumps(data))
        self.assertEqual(json.loads(self.invoke('--status'))['sim'],'unknown')
        self.invoke('--tick');self.assertFalse(self.actions.exists())
        self.invoke('--sim-result','4_1',input='+CME ERROR: 10')
        (self.path/'sys/devices/usb/modem/devnum').write_text('3')
        self.invoke('--tick')
        self.assertEqual(json.loads(self.invoke('--status'))['sim'],'unknown')
        self.assertFalse((self.run/'blocked').exists())

    def test_pppoe_priority_vlan_literal_credentials_and_confirmation(self):
        self.fixture()
        self.assertTrue(self.request()['ok'])
        data=json.loads(self.state.read_text())
        self.assertEqual(data['network.wan.username'],"user'$()")
        self.assertEqual(data['network.wan.password'],"secret'$(touch SHOULD_NOT_EXIST)")
        self.assertEqual(data['network.wan.device'],'wan.41')
        self.assertEqual(data['mwan3.aw_cell.metric'],'1')
        self.assertEqual(data['mwan3.aw_wired.metric'],'2')
        self.assertEqual(data['network.lan.ipaddr'],'192.168.31.1')
        status=json.loads(self.invoke('--status'));self.assertTrue(status['pending']);self.assertNotIn('password',status)
        self.invoke('--rpc','call','confirm');self.assertFalse(self.pending.exists())
        self.assertFalse((Path.cwd()/'SHOULD_NOT_EXIST').exists())

    def test_invalid_settings_and_custom_policy_never_mutate(self):
        self.fixture();before=self.state.read_bytes()
        for args in ({'mode':'ap'},{'vlan':'4095'},{'mtu':'1500'},{'username':''},{'password':'bad\nvalue'}):
            self.assertIn('error',self.request(**args));self.assertEqual(self.state.read_bytes(),before)
        for key,value in [('mwan3.globals.aw1000_enabled','0'),('mwan3.aw_default.family','ipv6'),
                ('mwan3.aw_default.dest_ip','192.0.2.0/24'),('mwan3.aw_failover.last_resort','default'),
                ('mwan3.aw_default.use_policy','custom'),('mwan3.aw_failover.use_member',['aw_cell']),
                ('mwan3.aw_wired.interface','other')]:
            data=json.loads(before);data[key]=value;self.state.write_text(json.dumps(data))
            self.assertIn('error',self.request())
            self.assertEqual(json.loads(self.state.read_text()),data)
        data=json.loads(before);data['mwan3.custom']='rule';self.state.write_text(json.dumps(data))
        self.assertIn('error',self.request());self.assertFalse(self.pending.exists())

    def test_unconfirmed_change_rolls_back_on_timeout_or_reboot(self):
        self.fixture()
        for reboot in (False,True):
            self.assertTrue(self.request()['ok'])
            for name in ('network','mwan3','aw1000','qmodem'):(self.config/name).write_text('changed')
            if reboot:(self.pending/'boot').write_text('previous-boot')
            else:(self.pending/'deadline').write_text('0')
            self.invoke('--tick')
            for name in ('network','mwan3','aw1000','qmodem'):
                self.assertEqual((self.config/name).read_text(),'original-'+name)
            self.assertFalse(self.pending.exists())

    def test_generated_ui_menu_acl_and_syntax(self):
        self.fixture()
        base=self.tree/'files'
        acl=json.loads((base/'usr/share/rpcd/acl.d/aw1000-internet.json').read_text())['aw1000-internet']
        self.assertEqual(acl['read']['ubus']['aw1000.internet'],['status'])
        self.assertEqual(acl['write']['ubus']['aw1000.internet'],['apply','confirm'])
        self.assertEqual((base/'www/luci-static/resources/view/aw1000/internet.js').read_text().count('reject:true'),3)
        menu=json.loads((base/'usr/share/luci/menu.d/aw1000-internet.json').read_text())
        self.assertIn('admin/network/aw1000_internet',menu)
        subprocess.run(['sh','-n',str(self.internet)],check=True)
        subprocess.run(['node','--check',str(base/'www/luci-static/resources/view/aw1000/internet.js')],check=True)


    def test_sim_gate_retries_failed_mwan_transition(self):
        self.fixture()
        tool=self.path/'bin/mwan';original=tool.read_text()
        tool.write_text('#!/bin/sh\nexit 1\n')
        self.invoke('--sim-result','4_1',input='+CME ERROR: 10')
        failed=subprocess.run(['sh',str(self.internet),'--tick'],env=self.env,capture_output=True)
        self.assertNotEqual(failed.returncode,0)
        self.assertTrue((self.run/'blocked').exists())
        tool.write_text(original)
        self.invoke('--sim-result','4_1',input='ERROR');self.invoke('--tick')
        self.assertEqual(self.actions.read_text().splitlines(),['ifdown wwan0'])
        tool.write_text('#!/bin/sh\nexit 1\n')
        self.invoke('--sim-result','4_1',input='+CPIN: READY')
        failed=subprocess.run(['sh',str(self.internet),'--tick'],env=self.env,capture_output=True)
        self.assertNotEqual(failed.returncode,0)
        tool.write_text(original);self.invoke('--tick')
        self.assertEqual(self.actions.read_text().splitlines(),['ifdown wwan0','ifup wwan0'])

    def test_wired_only_has_no_cellular_fallback(self):
        self.fixture()
        state=json.loads(self.state.read_text());state.update({'network.wan':'interface','network.wwan0':'interface'})
        self.state.write_text(json.dumps(state))
        self.assertTrue(self.request(priority='wired_only',mode='dhcp')['ok'])
        state=json.loads(self.state.read_text())
        self.assertEqual(state['mwan3.aw_failover.use_member'],['aw_wired'])
        self.assertEqual(state['network.wan.defaultroute'],'1')
        self.assertEqual(state['network.wwan0.defaultroute'],'1')

    def test_cellular_only_has_no_wired_fallback(self):
        self.fixture()
        state=json.loads(self.state.read_text());state.update({'network.wan':'interface','network.wwan0':'interface'})
        self.state.write_text(json.dumps(state))
        self.assertTrue(self.request(priority='cellular_only',mode='dhcp')['ok'])
        state=json.loads(self.state.read_text())
        self.assertEqual(state['mwan3.aw_failover.use_member'],['aw_cell'])
        self.assertEqual(state['network.wan.defaultroute'],'1')
        self.assertEqual(state['network.wwan0.defaultroute'],'1')
        self.invoke('--rpc','call','confirm')
        self.assertTrue(self.request(priority='wired',mode='dhcp')['ok'])
        state=json.loads(self.state.read_text())
        self.assertEqual(state['mwan3.aw_failover.use_member'],['aw_wired','aw_cell'])
        self.assertEqual(state['network.wan.defaultroute'],'1')
        self.assertEqual(state['network.wwan0.defaultroute'],'1')

    def test_late_cellular_interface_obeys_single_mode(self):
        self.fixture()
        self.assertTrue(self.request(priority='wired_only',mode='dhcp')['ok'])
        state=json.loads(self.state.read_text());state['network.wwan0']='interface'
        self.state.write_text(json.dumps(state))
        self.invoke('--tick')
        state=json.loads(self.state.read_text())
        self.assertEqual(state['network.wwan0.defaultroute'],'1')

    def test_tick_restores_v182_routes_without_changing_single_policy(self):
        self.fixture()
        state=json.loads(self.state.read_text())
        state.update({'aw1000.internet.priority':'wired_only',
            'mwan3.aw_failover.use_member':['aw_wired'],
            'network.wan':'interface','network.wwan0':'interface',
            'network.wan.defaultroute':'1','network.wwan0.defaultroute':'0',
            'mwan3.wan.enabled':'1','mwan3.wwan0.enabled':'1','qmodem.4_1.enable_dial':'1'})
        self.state.write_text(json.dumps(state))
        self.invoke('--tick')
        state=json.loads(self.state.read_text())
        self.assertEqual(state['network.wwan0.defaultroute'],'1')
        self.assertEqual(state['mwan3.aw_failover.use_member'],['aw_wired'])
        self.assertEqual(state['mwan3.wan.enabled'],'1')
        self.assertEqual(state['mwan3.wwan0.enabled'],'1')
        self.assertEqual(state['qmodem.4_1.enable_dial'],'1')
        before=self.state.read_bytes()
        self.invoke('--tick')
        self.assertEqual(self.state.read_bytes(),before)
