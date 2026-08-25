"""Temporary real HTTP application for browser tests; authentication/Kube fixtures only."""
from pathlib import Path
from http.server import ThreadingHTTPServer
from errors import Error
from http_app import make_handler
from tenant_api import TenantAPI
from test_support import seeded


if __name__ == '__main__':
    kube, _ = seeded()
    def auth(headers):
        cookie = headers.get('Cookie', '')
        if cookie not in ('grafana_session=admin', 'grafana_session=member'):
            raise Error('login', 401)
        return dict(providerInstance='g1', subjectKind='uid', subjectId='a', login='A',
                    platformAdmin=cookie.endswith('admin')), 'token'
    server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *args: None)
    url = 'http://127.0.0.1:' + str(server.server_port)
    server.RequestHandlerClass = make_handler(TenantAPI(kube, b'key', False), auth, url,
                                              'https://grafana.example', Path(__file__).parent)
    print(url, flush=True)
    try: server.serve_forever()
    finally: server.server_close()
