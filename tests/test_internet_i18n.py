import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import test_project as project
internet = project.load('internet')


class InternetLanguages(unittest.TestCase):
    def test_english_and_chinese_render_with_system_gettext(self):
        script = r'''
const fs=require('node:fs'), assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
for(const lang of ['en','zh-cn']) {
  function E(tag,attrs,kids) {
    const node={tag,attrs:attrs||{},style:{},value:attrs&&attrs.value||'',children:[],
      addEventListener(){},appendChild(child){this.children.push(child);},
      removeChild(child){this.children.splice(this.children.indexOf(child),1);}};
    Object.defineProperty(node,'firstChild',{get(){return this.children[0];}});
    node.children=Array.isArray(kids)?kids:(kids==null?[]:[kids]);return node;
  }
  const translate=text=>lang==='zh-cn'?(input.catalog[text]||text):text;
  const view=new Function('view','rpc','poll','ui','E','_',input.source)(
    {extend:x=>x},{declare:()=>()=>Promise.resolve({})},{add(){}},
    {createHandlerFn:(owner,fn)=>fn.bind(owner)},E,translate);
  const result=view.render({});
  function texts(node){if(typeof node==='string')return node;return (node.children||[]).map(texts).join('\n');}
  const rendered=texts(result);
  assert.ok(rendered.includes(lang==='en'?'Internet access':'上网方式'));
  assert.ok(rendered.includes(lang==='en'?'Wired network only':'仅有线网络'));
  assert.ok(rendered.includes(lang==='en'?'Cellular network only':'仅蜂窝网络'));
  if(lang==='en') assert.ok(!/[\u4e00-\u9fff]/.test(rendered));
}
'''
        result = subprocess.run(['node','-e',script], input=json.dumps({'source':internet.UI,'catalog':internet.TRANSLATIONS}),
                                text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_real_catalog_package_and_translatable_rpc_errors(self):
        with tempfile.TemporaryDirectory() as d:
            tree=Path(d);internet.install(tree)
            package=tree/'package/custom/luci-i18n-aw1000-internet-zh-cn'
            self.assertIn('$(STAGING_DIR_HOSTPKG)/bin/po2lmo', (package/'Makefile').read_text())
            po=(package/'aw1000-internet.zh-cn.po').read_text()
            self.assertIn('msgid "Internet access"',po)
            self.assertIn('msgstr "上网方式"',po)
            menu=json.loads((tree/'files/usr/share/luci/menu.d/aw1000-internet.json').read_text())
            self.assertEqual(menu['admin/network/aw1000_internet']['title'],'Internet access')
            self.assertNotRegex(internet.UI,r'[\u4e00-\u9fff]')
            for message in re.findall(r'"error":"([^"]+)"', internet.RUNTIME):
                self.assertIn(message,internet.TRANSLATIONS)

    def test_vlmcsd_defaults_and_startup(self):
        features=project.load('features')
        with tempfile.TemporaryDirectory() as d:
            tree=Path(d);features.install(tree)
            config=(tree/'files/etc/config/vlmcsd').read_text()
            self.assertIn("option enabled '1'",config)
            self.assertIn("option auto_activate '1'",config)
            self.assertIn("option internet_access '0'",config)
            self.assertEqual((tree/'files/etc/rc.d/S99vlmcsd').readlink(),Path('../init.d/vlmcsd'))
