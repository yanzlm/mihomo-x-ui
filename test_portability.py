import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server as m
from integrate_dashboard import integrate, TAG


class PortableConfiguration(unittest.TestCase):
    def test_yaml_config_can_be_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.yaml'
            config.write_text('secret: example-only\nproxy-groups: []\n', encoding='utf8')
            with patch.object(m, 'ETC', config):
                self.assertEqual(m.load(config)['secret'], 'example-only')

    def test_first_source_only_enters_target_groups(self):
        source = {'id': '1234567890abcdef', 'name': 'demo', 'enabled': True}
        config = {'proxy-groups': [
            {'name': 'PROXY', 'type': 'select', 'proxies': ['DIRECT']},
            {'name': 'Private', 'type': 'select', 'proxies': ['DIRECT']},
        ], 'rules': ['MATCH,PROXY'], 'dns': {'enable': False}}
        with patch.object(m, 'TARGET_GROUPS', {'PROXY'}):
            result = m.reconcile(config, {'sources': []}, {'sources': [source]})
        self.assertEqual(result['proxy-groups'][0]['use'], ['demo'])
        self.assertNotIn('use', result['proxy-groups'][1])
        self.assertEqual(result['rules'], config['rules'])
        self.assertEqual(result['dns'], config['dns'])

    def test_initialize_without_http_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.yaml'
            config.write_text('secret: example\nproxy-groups:\n - name: PROXY\n   type: select\n   proxies: [DIRECT]\n', encoding='utf8')
            with patch.object(m, 'ETC', config), patch.object(m, 'DATA', root/'state'), patch.object(m, 'TARGET_GROUPS', {'PROXY'}):
                m.bootstrap()
                self.assertEqual(m.state(), {'sources': [], 'last_backup': None})
                m.bootstrap()  # Existing state is retained.

    def test_initialize_without_target_group_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.yaml'
            config.write_text('proxy-groups: []', encoding='utf8')
            with patch.object(m, 'ETC', config), patch.object(m, 'DATA', Path(tmp)/'state'):
                with self.assertRaises(m.Problem):
                    m.bootstrap()

    def test_initial_migration_has_no_unusable_web_undo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / 'config.yaml'
            config.write_text('proxy-providers:\n  demo:\n    type: http\n    url: https://example.com/sub\n', encoding='utf8')
            with patch.object(m, 'ETC', config), patch.object(m, 'DATA', root/'state'), patch.object(m, 'fetched', return_value=b'nodes'), patch.object(m, 'apply', side_effect=lambda old,new,body: dict(copy.deepcopy(new), last_backup='migration')):
                m.bootstrap()
                self.assertIsNone(m.state()['last_backup'])


class DashboardIntegration(unittest.TestCase):
    def test_backup_and_new_precache_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            index, worker = root/'index.html', root/'sw.js'
            index.write_text('<html><head></head></html>', encoding='utf8')
            worker.write_text('precache([{url:"./",revision:"old"}]);', encoding='utf8')
            backup = integrate(root, root/'backups')
            self.assertEqual((backup/'index.html').read_text(), '<html><head></head></html>')
            self.assertIn(TAG, index.read_text())
            self.assertNotIn('revision:"old"', worker.read_text())
            self.assertIsNone(integrate(root, root/'backups'))

    def test_unknown_worker_is_not_modified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = '<head></head>'
            (root/'index.html').write_text(original, encoding='utf8')
            (root/'sw.js').write_text('unknown worker', encoding='utf8')
            with self.assertRaises(ValueError):
                integrate(root, root/'backups')
            self.assertEqual((root/'index.html').read_text(), original)
            self.assertEqual((root/'sw.js').read_text(), 'unknown worker')


if __name__ == '__main__':
    unittest.main()
