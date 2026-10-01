"""Real local HTTP/runtime fixture; no live Kubernetes access."""
import json
import threading
import time
from pathlib import Path
from http.server import ThreadingHTTPServer
from errors import Error
from http_app import make_handler
from tenant_api import TenantAPI
from kueue_quota_control import KueueQuotaControl
from test_kueue_quota_control import ADMIN
from test_kueue_support import seed_kueue, CM


if __name__ == '__main__':
    kube, _, policies, _ = seed_kueue()
    lock = threading.RLock()
    control = KueueQuotaControl(kube, True)
    def auth(headers):
        cookie = headers.get('Cookie', '')
        if cookie not in ('grafana_session=admin', 'grafana_session=member'): raise Error('login', 401)
        return dict(ADMIN, login='A', platformAdmin=cookie.endswith('admin')), 'token'
    server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
    url = 'http://127.0.0.1:' + str(server.server_port)
    server.RequestHandlerClass = make_handler(TenantAPI(kube, b'key', False), auth, url,
        'https://grafana.example', Path(__file__).parent, lock, control)
    def loop():
        while True:
            with lock: control.tick()
            time.sleep(0.2)
    threading.Thread(target=loop, daemon=True).start()
    print(url, flush=True)
    try: server.serve_forever()
    finally: server.server_close()
