import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import configure
import compat


class ProcdTransactions(unittest.TestCase):
    # Minimal upstream boundary fixture; procd's JSON context is destructive
    # when a nested close/query runs, exactly as in rc.common/procd.sh.
    BEFORE = '''
start_service(){
    force_stop=0
    update_modem
}

stop_service(){ :; }
update_modem(){ config_foreach _update_modem modem-device; }
dial(){
\trc_procd _dial $1
}
_update_modem(){
    local target=1
    config_get enabled "$1" enable_dial
    [ "$enabled" != 0 ] || target=0
    data=$(_procd_ubus_call list | jq -r  ".$service.instances.modem_$1")
    [ "$data" == "null" ] && current_status=0 || current_status=1
    if [ "$current_status" != "$target" ]; then
        [ "$target" -eq 1 ] && dial $1 || hang $1
    fi
}
_modem_status(){
    data=$(_procd_ubus_call list | jq  -r ".$service.instances.$instances")
    printf '%s\\n' "$data"
}

_dial()
{
    procd_open_instance "modem_$1"
    procd_set_param command /usr/share/qmodem/modem_dial.sh "$1" dial
    procd_close_instance
}
'''
    HARNESS = r'''
service=qmodem_network
g_enabled=1
config_load(){ :; }
config_get(){
    local value=1
    [ "$3" != state ] || value=enabled
    if [ "$2" = 4_1 ] && [ "$3" = enable_dial ]; then value=$CELL_ENABLED; fi
    printf -v "$1" '%s' "$value"
}
config_foreach(){ "$1" 4_1; "$1" 5_1; }
procd_open_service(){ JSON='{"name":"qmodem_network","instances":{}}'; }
procd_open_instance(){ INSTANCE=$1; }
procd_set_param(){
    shift
    JSON=$(jq -c --arg k "$INSTANCE" --args '.instances[$k]={command:$ARGS.positional}' "$@" <<<"$JSON")
}
procd_close_instance(){ :; }
procd_close_service(){ printf '%s %s\n' "$1" "$JSON" >>"$CALLS"; JSON='{}'; }
# The method selection and open/call/close order mirror pinned rc.common.
rc_procd(){
    local method=set
    [ -n "$2" ] && method=add
    procd_open_service qmodem_network
    "$@"
    procd_close_service "$method"
}
ubus(){ printf '%s\n' "$LIVE"; }
_procd_ubus_call(){ JSON='{}'; ubus; }
_hang(){ JSON='{}'; printf 'hang %s\n' "$1" >>"$EVENTS"; }
hang(){ _hang "$1"; }
logger(){ :; }
'''

    def run_transaction(self, action, enabled="1", running=False, patched=True):
        source = compat.modem_procd_patch(self.BEFORE) if patched else self.BEFORE
        with tempfile.TemporaryDirectory() as tmp:
            calls, events = Path(tmp) / 'calls', Path(tmp) / 'events'
            env = dict(os.environ, CALLS=str(calls), EVENTS=str(events), CELL_ENABLED=enabled,
                       LIVE=json.dumps({'qmodem_network': {'instances': {
                           'modem_4_1': {'running': running}}}}))
            result = subprocess.run(['bash', '-c', self.HARNESS + source + '\n' + action],
                                    env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            requests = [(line.split(' ', 1)[0], json.loads(line.split(' ', 1)[1]))
                        for line in calls.read_text().splitlines()] if calls.exists() else []
            return requests, events.read_text() if events.exists() else ''

    def test_start_declares_both_instances_in_one_nonempty_set(self):
        calls, events = self.run_transaction('rc_procd start_service')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], 'set')
        self.assertEqual(set(calls[0][1]['instances']), {'modem_4_1', 'modem_5_1'})
        self.assertEqual(events, '')

    def test_regression_fixture_reproduces_original_empty_set(self):
        calls, _ = self.run_transaction('rc_procd start_service', patched=False)
        self.assertEqual(calls[-1], ('set', {}))

    def test_single_dial_adds_without_replacing_sibling(self):
        calls, _ = self.run_transaction('dial 4_1')
        self.assertEqual(calls[0][0], 'add')
        self.assertEqual(set(calls[0][1]['instances']), {'modem_4_1'})

    def test_disabled_running_instance_cleanup_preserves_outer_builder(self):
        calls, events = self.run_transaction('rc_procd start_service', enabled='0', running=True)
        self.assertEqual(events, 'hang 4_1\n')
        self.assertEqual(set(calls[0][1]['instances']), {'modem_5_1'})

    def test_reload_keeps_healthy_instance_and_starts_dead_instance(self):
        calls, _ = self.run_transaction('_update_modem 4_1', running=True)
        self.assertEqual(calls, [])
        calls, _ = self.run_transaction('_update_modem 4_1', running=False)
        self.assertEqual(calls[0][0], 'add')

    def test_transform_is_idempotent_and_rejects_changed_upstream(self):
        patched = compat.modem_procd_patch(self.BEFORE)
        self.assertEqual(compat.modem_procd_patch(patched), patched)
        with self.assertRaises(RuntimeError):
            compat.modem_procd_patch(self.BEFORE.replace('stop_service', 'unknown_service'))


class RuntimeProtocol(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for protocol checks")
    def test_protocol_registers_runtime_and_uses_actual_device_without_reconfiguring(self):
        source = (ROOT / "files/qmodem_runtime.js").read_text()
        harness = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
let registered, options;
const network = {
  registerProtocol: (name, spec) => { registered = name; return spec; },
  getIfnameOf: x => typeof x === 'string' ? x : x.name
};
const form = { DummyValue: function() {} };
const protocol = new Function('network', 'form', '_', fs.readFileSync(0, 'utf8'))(network, form, x => x);
assert.equal(registered, 'qmodem_runtime');
const instance = Object.assign({sid: 'wwan0', _ubus: k => ({l3_device: 'wwan0_1'})[k]}, protocol);
assert.equal(instance.getIfname(), 'wwan0_1');
assert.equal(instance.containsDevice('wwan0_1'), true);
assert.equal(instance.containsDevice('eth0'), false);
instance.renderFormOptions({taboption: (...args) => { options = args; return {}; }});
assert.equal(options[1], form.DummyValue);
assert.equal(options[2], 'modem_config');
assert.equal(instance.getPackageName(), 'qmodem');
'''
        result = subprocess.run(["node", "-e", harness], input=source,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_protocol_is_in_the_generated_overlay(self):
        values = {k: "test" for k in ("lan_ip", "netmask", "ssid_24g", "ssid_5g",
                                      "wifi_password", "root_password_hash", "country_code")}
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp)
            configure.overlay(tree, values)
            protocol = tree / "files/www/luci-static/resources/protocol/qmodem_runtime.js"
            self.assertEqual(protocol.read_bytes(), (ROOT / "files/qmodem_runtime.js").read_bytes())
            self.assertFalse((tree / "files/usr/sbin/aw1000-led-at").exists())
