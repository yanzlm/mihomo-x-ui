import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import stack


class StackConfiguration(unittest.TestCase):
    def args(self):
        return argparse.Namespace(panel_domain='panel.example.com', xui_domain='xui.example.com', node_port=24443)

    def test_domains_and_ports_are_validated(self):
        for value in ['x;id.example.com', 'https://example.com', 'bad\n.example.com', '127.0.0.1', 'localhost', '-bad.example.com']:
            with self.subTest(value=value), self.assertRaises(ValueError): stack.domain(value)
        self.assertEqual(stack.domain('Panel.Example.COM'), 'panel.example.com')

    def test_default_entry_is_plan_only(self):
        with patch.object(stack, 'apply') as apply, patch('builtins.print') as output:
            stack.main(['--panel-domain','panel.example.com','--xui-domain','xui.example.com','--server-ip','8.8.8.8'])
            apply.assert_not_called()
            self.assertIn('plan-only', output.call_args.args[0])

    def test_private_and_cn_rules_precede_final_proxy(self):
        cfg = stack.render_mihomo('example-secret')
        self.assertEqual(cfg['rules'][-1], 'MATCH,PROXY')
        self.assertIn('RULE-SET,cn-domains,DIRECT', cfg['rules'][:-1])
        self.assertIn('IP-CIDR,192.168.0.0/16,DIRECT,no-resolve', cfg['rules'][:-1])
        self.assertEqual(cfg['proxy-groups'][0]['proxies'], ['REJECT'])
        self.assertEqual(cfg['listeners'][0]['listen'], '127.0.0.1')
        self.assertEqual(cfg['dns']['nameserver'], ['https://1.1.1.1/dns-query#PROXY'])

    def test_caddy_uses_separate_origins_and_preserves_paths(self):
        text = stack.render_caddy(self.args())
        for part in ['panel.example.com {', 'xui.example.com {', 'handle /sub/*', '127.0.0.1:2053', '127.0.0.1:2096']:
            self.assertIn(part, text)
        self.assertNotIn('handle_path /sub/*', text)

    def test_bridge_targets_only_expected_inbound(self):
        bridge = stack.bridge(24443)
        self.assertEqual(bridge['outbound']['settings']['servers'][0], {'address':'127.0.0.1','port':17891})
        self.assertEqual(bridge['rule']['inboundTag'], ['inbound-24443'])

    def test_seed_only_fresh_database_and_bind_internal_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            dbpath = Path(tmp)/'test.db'
            with closing(sqlite3.connect(dbpath)) as db, db:
                db.execute('CREATE TABLE settings (id INTEGER PRIMARY KEY, key TEXT, value TEXT)')
                db.execute('CREATE TABLE inbounds (id INTEGER PRIMARY KEY)')
                db.execute('INSERT INTO settings (key,value) VALUES ("webListen","")')
                db.execute('INSERT INTO settings (key,value) VALUES ("untouched","keep")')
            stack.seed_xui(dbpath,self.args())
            with closing(sqlite3.connect(dbpath)) as db, db:
                values = dict(db.execute('SELECT key,value FROM settings'))
                self.assertEqual(values['webListen'],'127.0.0.1')
                self.assertEqual(values['subListen'],'127.0.0.1')
                self.assertEqual(values['subURI'],'https://xui.example.com/sub/')
                self.assertEqual(values['untouched'],'keep')
                db.execute('INSERT INTO inbounds DEFAULT VALUES')
            with self.assertRaises(ValueError):stack.seed_xui(dbpath,self.args())

    def test_checksum_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            asset = {'url':'https://example.com/archive','sha256':'0'*64}
            with patch.object(stack.urllib.request,'urlopen',return_value=io.BytesIO(b'different')):
                with self.assertRaises(ValueError):stack.download(asset,Path(tmp)/'asset')

    def test_tar_traversal_and_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for kind in ['traversal','symlink']:
                archive=root/(kind+'.tar.gz')
                with tarfile.open(archive,'w:gz') as t:
                    entry=tarfile.TarInfo('../outside' if kind=='traversal' else 'link')
                    if kind=='symlink':entry.type=tarfile.SYMTYPE;entry.linkname='/etc/passwd'
                    t.addfile(entry)
                with self.subTest(kind=kind),self.assertRaises(ValueError):stack.extract_tar(archive,root/'output')
            self.assertFalse((root/'outside').exists())

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'test.zip'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../outside','bad')
            with self.assertRaises(ValueError):stack.extract_dashboard(archive,root/'output')

    def test_all_upstreams_are_pinned_to_official_hosts(self):
        lock=json.loads((stack.SOURCE/'deploy/versions.json').read_text())
        for component in lock['components'].values():
            self.assertNotIn('latest',component['version'])
            assets=list(component.get('assets',{}).values())
            if 'url' in component:assets.append(component)
            if 'menu' in component:assets.append(component['menu'])
            for asset in assets:
                self.assertRegex(asset['sha256'],r'^[a-f0-9]{64}$')
                self.assertRegex(asset['url'],r'^https://(github\.com|raw\.githubusercontent\.com|codeload\.github\.com)/')


if __name__=='__main__':unittest.main()
