import importlib.util
import json
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from errors import Error
from test_support import seeded


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('http_app'),'HTTP boundary required')
        from http_app import make_handler
        from tenant_api import TenantAPI
        k,_=seeded()
        def auth(headers):
            if headers.get('Cookie')!='grafana_session=valid': raise Error('login',401)
            return dict(providerInstance='g1',subjectKind='uid',subjectId='a',login='A',platformAdmin=False),'token'
        handler=make_handler(TenantAPI(k,b'key'),auth,'https://console.example','https://grafana.example',Path(__file__).parent)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_port)

    def tearDown(self):
        if hasattr(self,'server'): self.server.shutdown(); self.server.server_close(); self.thread.join()

    def request(self,path,method='GET',headers=None,data=None):
        req=urllib.request.Request(self.url+path,method=method,headers=headers or {},data=data)
        try: return urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc: return exc

    def test_unauthenticated_and_forged_admin_header(self):
        with self.request('/api/state',headers={'X-Grafana-Role':'Admin'}) as r: self.assertEqual(r.status,401)
        with self.request('/api/state',headers={'Cookie':'grafana_session=valid','X-Grafana-Role':'Admin'}) as r:
            self.assertEqual(r.status,200); self.assertEqual(json.load(r)['tenants'],[])

    def test_csrf_and_origin_and_body_limits(self):
        headers={'Cookie':'grafana_session=valid','Content-Type':'application/json','Origin':'https://console.example','X-CSRF-Token':'token'}
        for altered in ({'Origin':'https://evil.example'},{'X-CSRF-Token':'wrong'}):
            with self.request('/api/tenants','POST',dict(headers,**altered),b'{}') as r: self.assertEqual(r.status,403)
        with self.request('/api/tenants','POST',headers,b'x'*17000) as r: self.assertEqual(r.status,413)

    def test_session_identity_and_security_headers(self):
        with self.request('/api/session',headers={'Cookie':'grafana_session=valid'}) as r:
            data=json.load(r); self.assertEqual(data['principal']['subjectId'],'a')
            self.assertEqual(data['csrf'],'token'); self.assertEqual(r.headers['Cache-Control'],'no-store')
            self.assertIn("default-src 'self'",r.headers['Content-Security-Policy'])
