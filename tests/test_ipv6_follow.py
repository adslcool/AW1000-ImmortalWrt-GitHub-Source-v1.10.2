import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_project as project
ROOT = project.ROOT
compat = project.load('compat')


class Follow(unittest.TestCase):
    setUp = project.Provisioning.setUp

    def fixture(self, pd=False):
        self.board = self.path / 'board_name'
        self.env.update(AW1000_BOARD_FILE=str(self.board), AW1000_IPV6_RUN=str(self.path/'run'),
                        V6_STATE=str(self.path/'v6.json'), V6_CALLS=str(self.path/'calls'),
                        NFT_STATE=str(self.path/'nft'), RULE_STATE=str(self.path/'rules'))
        config = json.loads(self.state.read_text())
        config.update({'mwan3.globals.aw1000_enabled':'1', 'network.globals.ula_prefix':'fd12:3456::/48',
                       'aw1000.ipv6.original_classes':'', 'aw1000.ipv6.original_ra_default':'unset',
                       'aw1000.ipv6.original_ra_lifetime':'unset', 'dhcp.lan':'dhcp'})
        self.state.write_text(json.dumps(config))
        def client(dev, address, prefixes):
            return {'proto':'dhcpv6','up':True,'pending':False,'autostart':True,'l3_device':dev,
                    'ipv6-address':[{'address':address,'mask':64}], 'ipv6-prefix':prefixes,
                    'route':[{'mask':0,'nexthop':'fe80::1','valid':1800}]}
        self.clients={'wan6':client('wan','2408:1::2',
                                   [{'address':'2408:2::','mask':60,'class':'wan6'}] if pd else []),
                      'wwan0v6':client('wwan0','2409:1::2',
                                      [{'address':'2409:1::','mask':64,'class':'wwan0v6'}])}
        (self.path/'v6.json').write_text(json.dumps(self.clients))
        commands={
            'ubus':r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
p=Path(os.environ['V6_STATE']); d=json.loads(p.read_text())
with open(os.environ['V6_CALLS'],'a') as f:f.write('ubus '+' '.join(args)+'\n')
if args[1]=='network':sys.exit(0)
i=args[1].removeprefix('network.interface.')
if i=='lan':print(json.dumps({'l3_device':'br-lan'}));sys.exit(0)
if i not in d:sys.exit(1)
if args[2]=='status': print(json.dumps(d[i]))
elif args[2]=='up':
 d[i].update(up=True,autostart=True);p.write_text(json.dumps(d))
elif args[2]=='down':sys.exit(99)
''',
            'ip':r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
args=sys.argv[1:];p=Path(os.environ['RULE_STATE'])
if args[1:]==['route','show','table','61000']:
 print('unreachable default metric 32767');sys.exit(0)
if args[1:]==['rule','show']:
 print(p.read_text() if p.exists() else '');sys.exit(0)
with open(os.environ['V6_CALLS'],'a') as f:f.write('ip '+' '.join(args)+'\n')
if args[1:3]==['rule','del']:
 lines=p.read_text().splitlines() if p.exists() else []
 pref=args[args.index('pref')+1]+':'
 for index,line in enumerate(lines):
  if line.startswith(pref):
   lines.pop(index);p.write_text('\n'.join(lines)+'\n');sys.exit(0)
 sys.exit(2)
if args[1:3]==['rule','add']:
 with p.open('a') as f:f.write(args[args.index('pref')+1]+': owned\n')
''',
            'nft':r'''#!/usr/bin/env python3
import os,sys
from pathlib import Path
p=Path(os.environ['NFT_STATE']);args=sys.argv[1:]
if args[:2]==['list','table']:sys.exit(0 if p.exists() else 1)
if args[0]=='delete':p.unlink(missing_ok=True);sys.exit(0)
if os.environ.get('FAIL_NFT')=='1':sys.exit(1)
p.write_text(Path(args[1]).read_text())
''',
            'conntrack':'#!/bin/sh\nprintf "conntrack %s\\n" "$*" >> "$V6_CALLS"\n',
            'odhcpd':'#!/bin/sh\nprintf "odhcpd %s\\n" "$*" >> "$V6_CALLS"\n'}
        for name,text in commands.items():
            p=self.path/'bin'/name;p.write_text(text);p.chmod(0o755)
        script=(ROOT/'files/aw1000-ipv6-follow.sh').read_text().replace('/etc/init.d/odhcpd',str(self.path/'bin/odhcpd'))
        self.script=self.path/'follow';self.script.write_text(script)
        self.rows=json.dumps([{'enabled':True,'target':'wan'}, {'enabled':True,'target':'wwan0'}])

    def run_follow(self, choice, expected=0):
        p=subprocess.run(['sh',str(self.script),choice,self.rows],env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,expected,p.stderr)
        if expected==0 and choice!='--restore':
            return json.loads((self.path/'run/status.json').read_text())

    def test_wired_without_pd_uses_nat66_and_keeps_both_clients_up(self):
        self.fixture()
        report=self.run_follow('wan')
        self.assertEqual(report['mode'],'nat66')
        policy=(self.path/'nft').read_text()
        self.assertIn('snat to 2408:1::2',policy)
        self.assertIn('ip6 saddr fd12:3456::/48',policy)
        self.assertNotIn('ip6 saddr 2409:1::/64 return',policy)
        self.assertEqual(json.loads(self.state.read_text())['network.lan.ip6class'],['local'])
        self.assertNotIn(' down ',(self.path/'calls').read_text())
        self.assertTrue(all(x['up'] for x in json.loads((self.path/'v6.json').read_text()).values()))

    def test_native_prefix_and_default_route_switch_together(self):
        self.fixture(pd=True)
        self.assertEqual(self.run_follow('wan')['mode'],'native')
        policy=(self.path/'nft').read_text();self.assertNotIn('snat to',policy)
        self.assertIn('ip6 saddr 2408:2::/60 return',policy)
        self.assertEqual(json.loads(self.state.read_text())['network.lan.ip6class'],['local','wan6'])
        self.run_follow('wwan0')
        policy=(self.path/'nft').read_text()
        self.assertIn('oifname "wwan0" ip6 saddr 2409:1::/64 return',policy)
        self.assertNotIn('2408:2::',policy)
        self.assertEqual(json.loads(self.state.read_text())['network.lan.ip6class'],['local','wwan0v6'])
        calls=(self.path/'calls').read_text()
        self.assertIn('default via fe80::1 dev wwan0 src 2409:1::2 metric 10',calls)
        self.assertIn('conntrack -D -f ipv6 -s 2408:2::/60',calls)
        self.assertNotIn(' down ',calls)

    def test_no_healthy_line_blocks_egress_but_keeps_clients(self):
        self.fixture()
        self.assertEqual(self.run_follow('')['mode'],'unavailable')
        self.assertNotIn('snat to',(self.path/'nft').read_text())
        self.assertNotIn(' default via ',(self.path/'calls').read_text())
        self.assertIn('unreachable default',(self.path/'calls').read_text())
        self.assertEqual(json.loads(self.state.read_text())['dhcp.lan.ra_lifetime'],'0')
        self.assertTrue(all(x['up'] for x in json.loads((self.path/'v6.json').read_text()).values()))

    def test_missing_selected_ipv6_never_uses_standby(self):
        self.fixture();self.clients.pop('wan6')
        (self.path/'v6.json').write_text(json.dumps(self.clients))
        self.assertEqual(self.run_follow('wan')['mode'],'unavailable')
        self.assertNotIn('src 2409:',(self.path/'calls').read_text())

    def test_old_stopped_client_recovers_and_explicit_disable_is_respected(self):
        self.fixture();self.clients['wwan0v6'].update(up=False,autostart=False)
        (self.path/'v6.json').write_text(json.dumps(self.clients))
        self.run_follow('wan')
        self.assertIn('network.interface.wwan0v6 up {}',(self.path/'calls').read_text())
        self.clients['wwan0v6'].update(up=False,autostart=False)
        (self.path/'v6.json').write_text(json.dumps(self.clients))
        c=json.loads(self.state.read_text());c['network.wwan0v6.disabled']='1';self.state.write_text(json.dumps(c))
        self.assertEqual(self.run_follow('wwan0')['mode'],'unavailable')
        self.assertFalse(json.loads((self.path/'v6.json').read_text())['wwan0v6']['up'])

    def test_unchanged_state_does_not_reload_network_or_clear_flows(self):
        self.fixture();self.run_follow('wan')
        calls=(self.path/'calls').read_text()
        self.run_follow('wan')
        after=(self.path/'calls').read_text()
        self.assertEqual(after.count('network reload'),calls.count('network reload'))
        self.assertEqual(after.count('route flush'),calls.count('route flush'))
        self.assertNotIn('conntrack',after)

    def test_prefix_expiry_triggers_no_pd_fallback(self):
        self.fixture(pd=True);self.run_follow('wan')
        self.clients['wan6']['ipv6-prefix'][0]['preferred']=0
        (self.path/'v6.json').write_text(json.dumps(self.clients))
        self.assertEqual(self.run_follow('wan')['mode'],'nat66')
        self.assertNotIn('2408:2::',(self.path/'nft').read_text())

    def test_no_pd_fallback_can_be_disabled(self):
        self.fixture()
        c=json.loads(self.state.read_text());c['aw1000.ipv6.nat66_fallback']='0';self.state.write_text(json.dumps(c))
        self.assertEqual(self.run_follow('wan')['mode'],'unavailable')

    def test_foreign_rule_priority_is_not_overwritten(self):
        self.fixture();(self.path/'rules').write_text('8901: foreign rule\n')
        self.run_follow('wan',1)
        self.assertFalse((self.path/'nft').exists())

    def test_restore_removes_only_own_policy_and_restores_lan_settings(self):
        self.fixture();self.run_follow('wan');self.run_follow('--restore')
        self.assertFalse((self.path/'nft').exists())
        self.assertFalse((self.path/'run/signature').exists())
        self.assertNotIn('network.lan.ip6class',json.loads(self.state.read_text()))
        self.assertNotIn('dhcp.lan.ra_default',json.loads(self.state.read_text()))
    def test_standby_dhcpv6_and_router_addresses_have_narrow_exemptions(self):
        self.fixture();self.run_follow('wan')
        calls=(self.path/'calls').read_text()
        self.assertIn('pref 8895 oif wwan0 lookup main',calls)
        self.assertIn('pref 8894 from 2409:1::2/128 lookup main',calls)
        self.assertNotIn('pref 8894 from 2409:1::/64',calls)
        self.assertIn('pref 8893 to ff00::/8 lookup main',calls)

    def test_reenumerated_standby_device_and_address_refresh_exemptions(self):
        self.fixture();self.run_follow('wan')
        self.clients['wwan0v6']['l3_device']='wwan1'
        self.clients['wwan0v6']['ipv6-address'][0]['address']='2409:2::2'
        (self.path/'v6.json').write_text(json.dumps(self.clients))
        self.run_follow('wan')
        self.assertIn('oif wwan1',(self.path/'run/exemptions').read_text())
        self.assertIn('from 2409:2::2/128',(self.path/'run/exemptions').read_text())
        self.assertNotIn('2409:1::2',(self.path/'run/exemptions').read_text())

    def test_failed_nft_transaction_does_not_publish_success_or_change_lan(self):
        self.fixture();self.env['FAIL_NFT']='1'
        self.run_follow('wan',1)
        self.assertFalse((self.path/'run/signature').exists())
        self.assertNotIn('network.lan.ip6class',json.loads(self.state.read_text()))
        self.assertNotIn('network reload',(self.path/'calls').read_text())

    def test_mirror_paths_and_custom_sources_preserved(self):
        (self.path/'board').write_text('arcadyan,aw1000')
        c=json.loads(self.state.read_text());c['system.@imm_init[0]']='imm_init';self.state.write_text(json.dumps(c))
        etc=self.path/'etc';names=['apk/repositories.d/distfeeds.list','opkg/distfeeds.conf']
        content='https://downloads.immortalwrt.org/releases/25.12.2/packages/aarch64/base\nhttps://mirrors.vsean.net/openwrt/releases/25.12.2/targets/qualcommax/ipq807x/kmods/ABI\nhttps://custom.example/packages\n'
        for n in names:
            p=etc/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
        s=(ROOT/'files/98-aw1000-mirrors').read_text().replace('/tmp/sysinfo/board_name',str(self.path/'board')).replace('/etc/',str(etc)+'/')
        p=subprocess.run(['sh'],input=s,text=True,env=self.env,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        for n in names:
            s=(etc/n).read_text();self.assertEqual(s.count('https://mirrors.ustc.edu.cn/immortalwrt'),2)
            self.assertIn('/kmods/ABI',s);self.assertIn('/releases/25.12.2/packages/aarch64/base',s);self.assertIn('https://custom.example/packages',s)


class SteadyLEDs(unittest.TestCase):
    setUp = project.Provisioning.setUp

    def test_netdev_link_only_and_either_radio_fallback(self):
        leds=self.path/'leds';leds.mkdir()
        for name in ('green:wifi','green:internet'):
            d=leds/name;d.mkdir()
            for key,value in {'trigger':'none [netdev] timer', 'brightness':'1',
                              'device_name':'phy0-ap0','link':'1','rx':'1','tx':'1'}.items():
                (d/key).write_text(value)
        state=self.path/'wireless.json'
        data={'radio0':{'up':True,'config':{'band':'5g'},'interfaces':[{'ifname':'phy0-ap0','config':{'mode':'ap'}}]},
              'radio1':{'up':True,'config':{'band':'2g'},'interfaces':[{'ifname':'phy1-ap0','config':{'mode':'ap'}}]}}
        state.write_text(json.dumps(data))
        self.env.update(AW1000_BOARD_FILE=str(self.path/'board_name'), AW1000_LEDS_ROOT=str(leds),WIFI_STATE=str(state))
        p=self.path/'bin/ubus';p.write_text('#!/bin/sh\ncat "$WIFI_STATE"\n');p.chmod(0o755)
        def run():
            p=subprocess.run(['sh',str(ROOT/'files/aw1000-leds.sh')],env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
        run()
        self.assertEqual((leds/'green:wifi/device_name').read_text(),'phy0-ap0')
        self.assertEqual((leds/'green:internet/rx').read_text().strip(),'0')
        self.assertEqual((leds/'green:internet/tx').read_text().strip(),'0')
        off = self.path/'wifi-off'
        self.env['AW1000_WIFI_OFF_FILE'] = str(off)
        off.touch();run()
        self.assertEqual((leds/'green:wifi/brightness').read_text().strip(),'0')
        off.unlink();run()
        data['radio0']['up']=False;state.write_text(json.dumps(data));run()
        self.assertEqual((leds/'green:wifi/device_name').read_text().strip(),'phy1-ap0')
        data['radio1']['up']=False;state.write_text(json.dumps(data));run()
        self.assertEqual((leds/'green:wifi/brightness').read_text().strip(),'0')

    def test_signal_stays_off_without_data_even_with_fresh_rsrp_cache(self):
        leds=self.path/'leds';leds.mkdir()
        for name in ('red:signal','green:signal','blue:signal'):
            d=leds/name;d.mkdir();(d/'trigger').write_text('[none]');(d/'brightness').write_text('0')
        c=json.loads(self.state.read_text());c['mwan3.globals.aw1000_modem']='cell';self.state.write_text(json.dumps(c))
        cache=self.path/'cache_info_cell';cache.write_text(json.dumps({'info':[{'key':'RSRP','value':'-85'}]}))
        rundir=self.path/'cell_dir';rundir.mkdir();(rundir/'aw1000-sim-ready').write_text('1')
        self.env.update(AW1000_BOARD_FILE=str(self.path/'board_name'),AW1000_LEDS_ROOT=str(leds),AW1000_MODEM_CACHE=str(self.path),AW1000_MODEM_RUN=str(self.path))
        cmd=['sh',str(ROOT/'files/aw1000-leds.sh')]
        self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
        self.assertEqual((leds/'blue:signal/brightness').read_text().strip(),'0')
        self.assertEqual((leds/'green:signal/brightness').read_text().strip(),'0')
        self.assertEqual((leds/'red:signal/brightness').read_text().strip(),'0')
        os.utime(cache,(1,1))
        self.assertEqual(subprocess.run(cmd,env=self.env,capture_output=True).returncode,0)
        self.assertEqual((leds/'blue:signal/brightness').read_text().strip(),'0')

    def test_cellular_led_requires_sim_and_live_matching_data_session(self):
        leds=self.path/'leds';leds.mkdir()
        for color in ('green','blue','red'):
            d=leds/(color+':5g');d.mkdir();(d/'trigger').write_text('none [netdev]')
            (d/'brightness').write_text('1');(d/'tx').write_text('0');(d/'rx').write_text('0')
        c=json.loads(self.state.read_text())
        c.update({'mwan3.globals.aw1000_modem':'cell','qmodem.cell.alias':'wwan0'})
        self.state.write_text(json.dumps(c))
        rundir=self.path/'cell_dir';rundir.mkdir()
        (rundir/'cell.pid').write_text(str(os.getpid()))
        start='12345'
        proc=self.path/'proc'/str(os.getpid());proc.mkdir(parents=True)
        (proc/'stat').write_text(' '.join(['0']*21+[start]))
        (proc.parent/'uptime').write_text(Path('/proc/uptime').read_text())
        (rundir/'cm.start').write_text(start)
        status=self.path/'status.json'
        good={'up':True,'proto':'qmodem_runtime','l3_device':'wwan0',
              'ipv4-address':[{'address':'10.1.1.2'}],'route':[{'mask':0,'target':'0.0.0.0'}]}
        status.write_text(json.dumps(good))
        p=self.path/'bin/ubus';p.write_text("#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\nif sys.argv[2]=='network.wireless': print('{}')\nelif sys.argv[2]=='network.interface.'+os.environ.get('CELL_INTERFACE','wwan0'): print(Path(os.environ['CELL_STATUS']).read_text())\nelse: sys.exit(1)\n");p.chmod(0o755)
        self.env.update(AW1000_BOARD_FILE=str(self.path/'board_name'),AW1000_LEDS_ROOT=str(leds),
                        AW1000_MODEM_RUN=str(self.path),AW1000_PROC_ROOT=str(self.path/"proc"),CELL_STATUS=str(status))
        def run(expected):
            (proc.parent/'uptime').write_text(Path('/proc/uptime').read_text())
            p=subprocess.run(['sh',str(ROOT/'files/aw1000-leds.sh')],env=self.env,capture_output=True,text=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual((leds/'green:5g/brightness').read_text().strip(),str(expected))
            self.assertEqual((leds/'green:5g/trigger').read_text().strip(),'none')
            self.assertEqual((leds/'blue:5g/brightness').read_text().strip(),'0')
        run(0)  # Missing readiness snapshot: fail closed even if USB link exists.
        (rundir/'aw1000-sim-ready').write_text('0');run(0)
        (rundir/'aw1000-sim-ready').write_text('1');run(1)
        # No mwan3 registration: discover exactly one supported QModem instance.
        c.pop('mwan3.globals.aw1000_modem')
        c.update({'qmodem.cell':'modem-device','qmodem.cell.name':'RG500Q-EA','qmodem.cell.data_interface':'usb'})
        self.state.write_text(json.dumps(c));run(1)
        diagnostic=json.loads((self.path/'led-status.json').read_text())
        self.assertEqual(diagnostic['modem'],'cell')
        self.assertEqual(diagnostic['modem_source'],'qmodem_discovery')
        c.update({'qmodem.second':'modem-device','qmodem.second.name':'RG500Q-EA','qmodem.second.data_interface':'usb','qmodem.second.alias':'wwan1'})
        self.state.write_text(json.dumps(c));run(0)
        self.assertEqual(json.loads((self.path/'led-status.json').read_text())['reason'],'ambiguous_modems')
        c={k:v for k,v in c.items() if not k.startswith('qmodem.second')}
        c['qmodem.cell.name']='other-modem'
        self.state.write_text(json.dumps(c));run(0)
        c['qmodem.cell.name']='RG500Q-EA'
        self.state.write_text(json.dumps(c));run(1)
        c['mwan3.globals.aw1000_modem']='cell'
        self.state.write_text(json.dumps(c))
        helper=self.path/'bin/sim-helper'
        helper.write_text('#!/bin/sh\n[ "${SIM_STATE:-unknown}" != unsupported ] || exit 2\necho "${SIM_STATE:-unknown}"\n');helper.chmod(0o755)
        self.env['AW1000_INTERNET_HELPER']=str(helper)
        self.env['AW1000_INTERNET_RUN']=str(self.path/'eligibility')
        self.env['AW1000_MWAN_TRACK']=str(self.path/'tracks')
        self.env['SIM_STATE']='ready';run(1)
        # Hotfix on an older installed helper which cannot resolve an unbound modem.
        c.pop('mwan3.globals.aw1000_modem');self.state.write_text(json.dumps(c))
        self.env['SIM_STATE']='unknown';run(1)
        self.assertEqual(json.loads((self.path/'led-status.json').read_text())['sim_source'],'legacy_snapshot')
        self.env['SIM_STATE']='absent';run(0)
        c['mwan3.globals.aw1000_modem']='cell';self.state.write_text(json.dumps(c))
        # Confirmed unknown can light only with fresh native online proof.
        self.env['SIM_STATE']='unknown'
        (rundir/'aw1000-sim-ready').write_text('0');run(0)
        track=self.path/'tracks/wwan0';track.mkdir(parents=True)
        (track/'PID').write_text(str(os.getpid()))
        (track/'TIME').write_text(Path('/proc/uptime').read_text().split('.')[0])
        (track/'STATUS').write_text('online');run(1)
        diagnostic=json.loads((self.path/'led-status.json').read_text())
        self.assertTrue(diagnostic['fresh_tracker_online'])
        result=subprocess.run(['sh',str(ROOT/'files/aw1000-leds.sh'),'--status'],env=self.env,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),diagnostic)
        self.env['SIM_STATE']='unsupported';run(1)
        self.env['SIM_STATE']='unknown'
        for unavailable in ('absent','locked','failed'):
            self.env['SIM_STATE']=unavailable;run(0)
        self.env['SIM_STATE']='unknown'
        (track/'TIME').write_text('0');run(0)
        (track/'TIME').write_text(Path('/proc/uptime').read_text().split('.')[0])
        (track/'STATUS').write_text('offline');run(0)
        (track/'STATUS').write_text('online')
        (self.path/'eligibility').mkdir()
        (self.path/'eligibility/blocked').write_text('wwan0');run(0)
        self.env['SIM_STATE']='ready';run(1)
        self.env['SIM_STATE']='unknown'
        (self.path/'eligibility/blocked').unlink();run(1)
        self.env['SIM_STATE']='ready'
        (rundir/'aw1000-sim-ready').write_text('1')

        for update in ({'up':False},{'ipv4-address':[]},{'route':[]},{'l3_device':'wan'},{'proto':'dhcp'}):
            status.write_text(json.dumps({**good,**update}));run(0)
        status.write_text(json.dumps(good));(rundir/'cm.start').write_text('0');run(0)
        (rundir/'cm.start').write_text(start);(rundir/'cell.pid').write_text('99999999');run(0)
        (rundir/'cell.pid').write_text(str(os.getpid()))
        c['qmodem.main.enable_dial']='0';self.state.write_text(json.dumps(c));run(0)

    def test_native_instance_interface_can_use_different_kernel_device(self):
        leds=self.path/'leds';leds.mkdir()
        for color in ('green','blue','red'):
            for kind in ('5g','signal'):
                d=leds/(color+':'+kind);d.mkdir();(d/'trigger').write_text('none');(d/'brightness').write_text('0')
        config=json.loads(self.state.read_text())
        config.update({'qmodem.4_1':'modem-device','qmodem.4_1.name':'rg500q-ea',
            'qmodem.4_1.data_interface':'usb','qmodem.usb0':'modem-slot','qmodem.usb0.alias':'wwan0',
            'network.4_1':'interface','network.4_1.modem_config':'4_1','network.4_1.device':'wwan0'})
        self.state.write_text(json.dumps(config))
        run=self.path/'4_1_dir';run.mkdir()
        (run/'aw1000-sim-ready').write_text('1');(run/'4_1.pid').write_text(str(os.getpid()));(run/'cm.start').write_text('11197')
        proc=self.path/'proc'/str(os.getpid());proc.mkdir(parents=True)
        (proc/'stat').write_text(' '.join(['0']*21+['11197']))
        (proc.parent/'uptime').write_text(Path('/proc/uptime').read_text())
        self.env.update(AW1000_BOARD_FILE=str(self.path/'board_name'),AW1000_LEDS_ROOT=str(leds),
            AW1000_MODEM_RUN=str(self.path),AW1000_PROC_ROOT=str(proc.parent))
        ubus=self.path/'bin/ubus'
        ubus.write_text('''#!/bin/sh
case "$2" in
network.wireless) echo '{}';;
network.interface.4_1) echo '{"up":true,"proto":"qmodem_runtime","device":"wwan0","l3_device":"wwan0","ipv4-address":[{"address":"10.71.129.81","mask":30}],"route":[{"target":"0.0.0.0","mask":0,"nexthop":"10.71.129.82"}]}';;
*) exit 1;;
esac
''');ubus.chmod(0o755)
        def check(expected):
            result=subprocess.run(['sh',str(ROOT/'files/aw1000-leds.sh')],env=self.env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            status=json.loads((self.path/'led-status.json').read_text())
            self.assertEqual(status['cellular_led'],expected)
            self.assertEqual(status['interface'],'4_1');self.assertEqual(status['device'],'wwan0')
            self.assertEqual((leds/'green:5g/brightness').read_text().strip(),str(int(expected)))
            self.assertEqual((leds/'green:signal/brightness').read_text().strip(),str(int(expected)))
            for color in ('red','blue'):
                self.assertEqual((leds/(color+':signal')/'brightness').read_text().strip(),'0')
        check(True)
        self.assertEqual(json.loads((self.path/'led-status.json').read_text())['modem_source'],'qmodem_discovery')
        config['network.4_1.modem_config']='other';self.state.write_text(json.dumps(config));check(False)
        config['network.4_1.modem_config']='4_1';config['mwan3.globals.aw1000_modem']='4_1'
        self.state.write_text(json.dumps(config));check(True)

    def test_migration_removes_nonexistent_led_and_preserves_speed_modes(self):
        leds=self.path/'leds';leds.mkdir()
        for name in ('green:wifi','green:internet','green:wan'):
            d=leds/name;d.mkdir();(d/'trigger').write_text('none [netdev] timer')
            for key in ('brightness','rx','tx','link'):(d/key).write_text('0')
            (d/'device_name').write_text('phy0-ap0')
        config=json.loads(self.state.read_text())
        config.update({'system.bad':'led','system.bad.sysfs':'ipq::led9',
                       'system.port':'led','system.port.sysfs':'green:wan','system.port.trigger':'netdev',
                       'system.port.mode':'tx rx link_10 link_100 link_1000 link_2500'})
        self.state.write_text(json.dumps(config))
        self.env.update(AW1000_BOARD_FILE=str(self.path/'board_name'),AW1000_LEDS_ROOT=str(leds))
        text=(ROOT/'files/aw1000-leds.sh').read_text().replace('/etc/init.d/led',str(self.path/'bin/logger'))
        p=subprocess.run(['sh','-s','--','--configure'],input=text,env=self.env,capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        after=json.loads(self.state.read_text())
        self.assertNotIn('system.bad',after)
        self.assertEqual(after['system.port.mode'],'link_10 link_100 link_1000 link_2500')
        self.assertEqual(after['system.led_wifi.mode'],'link')
        self.assertEqual(after['system.led_wifi.dev'],'phy0-ap0')


class ModemStatus(unittest.TestCase):
    setUp = project.Provisioning.setUp

    def test_managed_status_uses_exact_live_interface_without_at_guessing(self):
        original='''get_connect_status()
{
    connect_status="No"
    driver=$(get_driver)
    add_plain_info_entry "connect_status" "$connect_status" "Connect Status"
}
'''
        patched=compat.modem_interface_patch(compat.modem_status_patch(original))
        self.assertEqual(compat.modem_interface_patch(compat.modem_status_patch(patched)),patched)
        patched=patched.replace('/tmp/sysinfo/board_name',str(self.path/'board_name'))
        data=json.loads(self.state.read_text())
        data.update({'qmodem.cell.host_ipv4_owner':'netifd','qmodem.cell.alias':'wwan0'})
        self.state.write_text(json.dumps(data))
        status=self.path/'status';self.env['MODEM_STATUS']=str(status)
        p=self.path/'bin/ubus';p.write_text('#!/bin/sh\ncat "$MODEM_STATUS"\n');p.chmod(0o755)
        script='add_plain_info_entry() { echo "$2"; }\nget_driver() { echo AT_SHOULD_NOT_RUN >&2; }\n'+patched+'\nconfig_section=cell\nget_connect_status\n'
        good={'up':True,'proto':'qmodem_runtime','l3_device':'wwan0',
              'ipv4-address':[{'address':'10.1.1.2'}],'route':[{'target':'0.0.0.0','mask':0}]}
        for overrides,expected in [({},'Yes'),({'up':False},'No'),({'l3_device':'wan'},'No'),
                                   ({'proto':'dhcp'},'No'),({'route':[]},'No'),({'ipv4-address':[]},'No')]:
            status.write_text(json.dumps({**good,**overrides}))
            p=subprocess.run(['sh'],input=script,env=self.env,text=True,capture_output=True)
            self.assertEqual(p.returncode,0,p.stderr)
            self.assertEqual(p.stdout.strip(),expected)
            self.assertNotIn('AT_SHOULD_NOT_RUN',p.stderr)
        data.pop('qmodem.cell.alias')
        data.update({'network.cell.device':'wwan0','network.cell.modem_config':'cell'})
        self.state.write_text(json.dumps(data));status.write_text(json.dumps(good))
        result=subprocess.run(['sh'],input=script,env=self.env,text=True,capture_output=True)
        self.assertEqual(result.stdout.strip(),'Yes')
        data['network.cell.modem_config']='another';self.state.write_text(json.dumps(data))
        result=subprocess.run(['sh'],input=script,env=self.env,text=True,capture_output=True)
        self.assertEqual(result.stdout.strip(),'No')

