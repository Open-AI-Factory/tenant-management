"""Real HTTP boundary checks for the authenticated, simulation-only demo."""
import copy
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer
from errors import Error
from http_app import make_handler
from tenant_api import TenantAPI
from test_support import seeded


class QuotaDemoHttpTests(unittest.TestCase):
    def setUp(self):
        self.kube, _ = seeded()
        self.before = copy.deepcopy(self.kube.objects)
        def auth(headers):
            identity = headers.get('Cookie')
            if identity not in ('grafana_session=admin', 'grafana_session=member'):
                raise Error('login', 401)
            return dict(providerInstance='g1', subjectKind='uid', subjectId='a',
                        login='A', platformAdmin=identity.endswith('admin')), 'token'
        handler = make_handler(TenantAPI(self.kube, b'key', False), auth,
                               'https://console.example', 'https://grafana.example', Path(__file__).parent)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path, method='GET', body=None, role='admin', extra=None):
        headers = {'Cookie': 'grafana_session=' + role, 'Content-Type': 'application/json',
                   'Origin': 'https://console.example', 'X-CSRF-Token': 'token'}
        headers.update(extra or {})
        payload = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, method=method, headers=headers, data=payload)
        try: response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc: response = exc
        with response:
            data = response.read()
            return response.status, json.loads(data) if 'application/json' in response.headers['Content-Type'] else data

    def test_authenticated_admin_page_assets_and_state(self):
        status, page = self.request('/quota-demo')
        self.assertEqual(status, 200)
        self.assertIn('예측으로 조절하는'.encode(), page)
        for asset in ('quota_simulator.js', 'quota_simulator.css'):
            self.assertEqual(self.request('/quota-demo/' + asset)[0], 200)
        status, state = self.request('/api/quota-demo/state')
        self.assertEqual(status, 200); self.assertEqual(state['quotas'], {'tenant-a': 1, 'tenant-b': 1})

    def test_member_and_forged_header_cannot_read_or_mutate_demo(self):
        for path, method, body in [('/quota-demo', 'GET', None),
                                   ('/quota-demo/quota_simulator.js', 'GET', None),
                                   ('/api/quota-demo/state', 'GET', None),
                                   ('/api/quota-demo/reset', 'POST', {})]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, method, body, 'member', {'X-Grafana-Role': 'Admin'})[0], 403)

    def test_no_login_cannot_read_demo(self):
        for path in ('/quota-demo', '/api/quota-demo/state'):
            self.assertEqual(self.request(path, role='invalid')[0], 401)

    def test_mutations_require_same_origin_and_csrf(self):
        for extra in ({'Origin': 'https://evil.example'}, {'X-CSRF-Token': 'wrong'}):
            self.assertEqual(self.request('/api/quota-demo/reset', 'POST', {}, extra=extra)[0], 403)
        self.assertEqual(self.request('/api/quota-demo/reset', 'POST', {})[0], 200)

    def test_scenario_and_reset_leave_real_tenants_unchanged_with_gates_closed(self):
        for _ in range(2):
            status, state = self.request('/api/quota-demo/scenario/next', 'POST', {})
            self.assertEqual(status, 200)
        self.assertEqual(state['quotas'], {'tenant-a': 3, 'tenant-b': 1})
        self.assertEqual(self.kube.objects, self.before)
        status, state = self.request('/api/quota-demo/reset', 'POST', {})
        self.assertEqual(status, 200); self.assertEqual(state['scenarioIndex'], 0)
        self.assertEqual(state['quotas'], {'tenant-a': 1, 'tenant-b': 1})
        self.assertEqual(self.kube.objects, self.before)

    def test_duplicate_json_and_nonfinite_numbers_are_rejected_atomically(self):
        for body in (b'{"now":0,"now":1,"observations":{}}', b'{"now":NaN,"observations":{}}'):
            self.assertEqual(self.request('/api/quota-demo/advance', 'POST', body)[0], 400)
        status, state = self.request('/api/quota-demo/state')
        self.assertEqual(status, 200); self.assertEqual(state['scenarioIndex'], 0)

    def test_wrong_methods_and_unknown_paths_do_not_change_state(self):
        self.assertEqual(self.request('/api/quota-demo/reset', 'PUT', {})[0], 404)
        self.assertEqual(self.request('/api/quota-demo/nope', 'POST', {})[0], 404)
        self.assertEqual(self.request('/quota-demo/../server.py')[0], 404)
        self.assertEqual(self.request('/api/quota-demo/state')[1]['scenarioIndex'], 0)

