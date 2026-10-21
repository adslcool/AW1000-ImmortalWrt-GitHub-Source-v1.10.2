'use strict';
'require form';
'require network';

// The netifd backend and PDP lifecycle belong to QModem. This module only
// supplies LuCI's missing protocol presentation; it does not start a dialer.
return network.registerProtocol('qmodem_runtime', {
	getI18n: function() {
		return _('QModem managed IPv4');
	},

	getIfname: function() {
		return this._ubus('l3_device') || this._ubus('device') || this.sid;
	},

	getPackageName: function() {
		return 'qmodem';
	},

	isFloating: function() {
		return true;
	},

	isVirtual: function() {
		return false;
	},

	getDevices: function() {
		return null;
	},

	containsDevice: function(ifname) {
		return network.getIfnameOf(ifname) === this.getIfname();
	},

	renderFormOptions: function(s) {
		var o = s.taboption('general', form.DummyValue, 'modem_config',
			_('QModem instance'),
			_('This interface is managed by QModem. Configure APN, dialing and routing in QModem.'));
		o.rmempty = false;
	}
});
