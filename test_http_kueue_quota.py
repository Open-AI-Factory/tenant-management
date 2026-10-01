import copy
import inspect
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
from kueue_quota_control import KueueQuotaControl
from test_kueue_quota_control import ADMIN, event
from test_kueue_support import seed_kueue, CQ, CM


class Tests(unittest.TestCase):
    def setUp(self):
        self.assertIn('kueue_control', inspect.signature(make_handler).parameters,
                      'Actual budget HTTP boundary is not connected')
        self.k, _, _, _ = seed_kueue()
        self.control = KueueQuotaControl(self.k, True, clock=lambda: 1791406800.0)
        def auth(headers):
            cookie = headers.get('Cookie')
            if cookie not in ('grafana_session=admin', 'grafana_session=member'):
                raise Error('login', 401)
            return dict(ADMIN, platformAdmin=cookie.endswith('admin')), 'token'
        handler = make_handler(TenantAPI(self.k, b'key', False), auth, 'https://console.example',
            'https://grafana.example', Path(__file__).parent, kueue_control=self.control)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        if hasattr(self, 'server'):
            self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self, path='/api/kueue-quota/state', method='GET', body=None, role='admin', extra=None):
        headers = {'Cookie': 'grafana_session=' + role, 'Content-Type': 'application/json',
                   'Origin': 'https://console.example', 'X-CSRF-Token': 'token'}
        headers.update(extra or {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.url + path, data=raw, headers=headers, method=method)
        try: response = urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc: response = exc
        with response:
            data = response.read()
            return response.status, json.loads(data) if 'application/json' in response.headers['Content-Type'] else data

    def test_admin_forecast_durably_changes_actual_fixture_queue(self):
        self.assertEqual(200, self.request('/kueue-quota')[0])
        for asset in ('kueue_quota.js', 'kueue_quota.css'):
            self.assertEqual(200, self.request('/kueue-quota/' + asset)[0])
        status, accepted = self.request('/api/kueue-quota/forecasts', 'POST', event(1791406800.0))
        self.assertEqual((200, 'Received'), (status, accepted['status']))
        self.assertEqual('t-a-1', json.loads(self.k.objects[CM + '/tenant-admin-budget-state']['data']['document'])['tenants']['t-a']['forecast']['event']['eventId'])
        self.control.tick()
        self.assertEqual(2, self.request()[1]['tenants']['t-a']['actual']['quota'])

    def test_general_anonymous_and_forged_admin_are_denied(self):
        for path in ('/kueue-quota', '/api/kueue-quota/state', '/kueue-quota/kueue_quota.js'):
            self.assertEqual(403, self.request(path, role='member', extra={'X-Grafana-Role': 'Admin'})[0])
            self.assertEqual(401, self.request(path, role='invalid')[0])

    def test_origin_csrf_and_methods(self):
        for extra in ({'Origin': 'https://evil.example'}, {'X-CSRF-Token': 'bad'}):
            self.assertEqual(403, self.request('/api/kueue-quota/forecasts', 'POST', event(1791406800.0), extra=extra)[0])
        for path in ('/api/kueue-quota/reset', '/api/kueue-quota/policies', '/kueue-quota/../server.py'):
            self.assertEqual(404, self.request(path, 'POST', {})[0])

    def test_strict_json_boundary_rejects_without_queue_writes(self):
        bad = [b'{"eventId":"one","eventId":"two"}', b'{"expectedDevices":NaN}',
               b'{"eventId":"\xff"}', b'{"eventId":"\ud800"}',
               b'{"deep":' + b'[' * 40 + b'0' + b']' * 40 + b'}']
        for raw in bad:
            self.assertEqual(400, self.request('/api/kueue-quota/forecasts', 'POST', raw)[0])
        self.assertEqual(413, self.request('/api/kueue-quota/forecasts', 'POST', b' ' * 32769)[0])
        self.assertFalse(any(method == 'PUT' and path.startswith(CQ) for method, path in self.k.calls))

    def test_disabled_missing_config_preserves_existing_gui(self):
        self.control.enabled = False
        self.assertFalse(self.request()[1]['enabled'])
        self.assertEqual(503, self.request('/api/kueue-quota/forecasts', 'POST', event(1791406800.0))[0])
        del self.k.objects[CM + '/tenant-admin-budget-policy']
        self.assertEqual(200, self.request('/')[0]); self.assertEqual(200, self.request('/api/session')[0])
        self.assertEqual('Unavailable', self.request()[1]['phase'])

    def test_store_failure_and_demo_have_no_actual_apply(self):
        before = copy.deepcopy(self.k.objects[CQ + '/example-tenant-queue-a'])
        self.k.fail_paths.add(CM + '/tenant-admin-budget-state')
        self.assertEqual(503, self.request('/api/kueue-quota/forecasts', 'POST', event(1791406800.0))[0])
        self.request('/api/quota-demo/scenario/next', 'POST', {})
        self.assertEqual(before, self.k.objects[CQ + '/example-tenant-queue-a'])
