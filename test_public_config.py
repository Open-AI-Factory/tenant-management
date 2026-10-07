"""Public manifest configuration, exercised without cluster access."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
CONFIG_KEYS = ('PUBLIC_ORIGIN', 'GRAFANA_URL', 'GRAFANA_INSTANCE_ID',
               'TLS_SECRET_NAME', 'TENANT_NODE_NAME')


class PublicConfigTests(unittest.TestCase):
    def build(self, overrides=None):
        env = os.environ.copy()
        for key in CONFIG_KEYS:
            env.pop(key, None)
        env.update(overrides or {})
        with tempfile.TemporaryDirectory() as folder:
            for source in ROOT.iterdir():
                if source.is_file() and source.suffix in ('.py', '.html', '.js', '.css', '.json') and source.name != 'resources.json':
                    shutil.copyfile(source, Path(folder) / source.name)
            result = subprocess.run([sys.executable, '-B', '-X', 'utf8', 'build.py'],
                                    cwd=folder, env=env, capture_output=True, text=True, timeout=15)
            manifest = Path(folder) / 'resources.json'
            items = json.loads(manifest.read_text(encoding='utf-8'))['items'] if manifest.exists() else None
        return result, items

    def deployment(self, items):
        return next(item for item in items if item['kind'] == 'Deployment')

    def test_default_manifest_has_placeholder_origins_and_no_node_pin(self):
        result, items = self.build()
        self.assertEqual(0, result.returncode, result.stderr)
        deployment = self.deployment(items)
        pod = deployment['spec']['template']['spec']
        env = {entry['name']: entry['value'] for entry in pod['containers'][0]['env']}
        self.assertEqual('https://tenant-admin.example.invalid', env['PUBLIC_ORIGIN'])
        self.assertEqual('https://grafana.example.invalid', env['GRAFANA_URL'])
        self.assertNotIn('nodeSelector', pod)
        self.assertEqual('tenant-admin-tls', next(v for v in pod['volumes'] if v['name'] == 'tls')['secret']['secretName'])
        self.assertEqual(1, deployment['spec']['replicas'])
        self.assertEqual('Recreate', deployment['spec']['strategy']['type'])
        self.assertEqual('monitoring', deployment['metadata']['namespace'])
        for key in ('TENANT_RECONCILE_ENABLED', 'TENANT_MUTATIONS_ENABLED', 'KUEUE_QUOTA_ENABLED'):
            self.assertEqual('false', env[key])
        service = next(item for item in items if item['kind'] == 'Service')
        self.assertEqual(30443, service['spec']['ports'][0]['nodePort'])

    def test_explicit_origins_match_embedded_ui_and_runtime(self):
        result, items = self.build({'PUBLIC_ORIGIN': 'https://console.example.org:30443/',
                                   'GRAFANA_URL': 'https://metrics.example.org:30300/',
                                   'GRAFANA_INSTANCE_ID': 'public-test-grafana',
                                   'TLS_SECRET_NAME': 'console-test-tls',
                                   'TENANT_NODE_NAME': 'control-node'})
        self.assertEqual(0, result.returncode, result.stderr)
        pod = self.deployment(items)['spec']['template']['spec']
        env = {entry['name']: entry['value'] for entry in pod['containers'][0]['env']}
        self.assertEqual('https://console.example.org:30443', env['PUBLIC_ORIGIN'])
        self.assertEqual('https://metrics.example.org:30300', env['GRAFANA_URL'])
        self.assertEqual('public-test-grafana', env['GRAFANA_INSTANCE_ID'])
        self.assertEqual({'kubernetes.io/hostname': 'control-node'}, pod['nodeSelector'])
        self.assertEqual('console-test-tls', next(v for v in pod['volumes'] if v['name'] == 'tls')['secret']['secretName'])
        code = next(item for item in items if item['kind'] == 'ConfigMap' and item['metadata']['name'] == 'tenant-admin')['data']
        for filename in ('app.js', 'index.html'):
            self.assertIn('https://metrics.example.org:30300', code[filename])
            self.assertNotIn('grafana.example.invalid', code[filename])
        for path in ('/login', '/d/ai-factory-home', '/d/tenant-management'):
            self.assertIn('https://metrics.example.org:30300' + path, code['index.html'])

    def test_invalid_origins_fail_before_manifest_is_written(self):
        invalid = ('', 'http://example.org', 'https://user:password@example.org',
                   'https://example.org/path', 'https://example.org?query=1',
                   'https://example.org#fragment', 'https://example.org:70000',
                   'https://example.org:', 'https://bad host.example',
                   "https://bad'host.example", 'https://example.org\n',
                   "https://[::1]'suffix", 'https://[::1]extra',
                   'https://example..org', 'https://-bad.example',
                   'https://example.org:0', 'https://999.999.999.999',
                   'https://127.1', 'https://0x7f000001')
        for key in ('PUBLIC_ORIGIN', 'GRAFANA_URL'):
            for origin in invalid:
                with self.subTest(key=key, origin=origin):
                    result, items = self.build({key: origin})
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(key, result.stderr)
                    self.assertIsNone(items)

    def test_origins_match_browser_canonicalization(self):
        result, items = self.build({'PUBLIC_ORIGIN': 'https://Console.Example.ORG:443/',
                                   'GRAFANA_URL': 'https://[2001:0db8::1]:8443/'})
        self.assertEqual(0, result.returncode, result.stderr)
        env = {entry['name']: entry['value'] for entry in self.deployment(items)['spec']['template']['spec']['containers'][0]['env']}
        self.assertEqual('https://console.example.org', env['PUBLIC_ORIGIN'])
        self.assertEqual('https://[2001:db8::1]:8443', env['GRAFANA_URL'])


if __name__ == '__main__':
    unittest.main()
