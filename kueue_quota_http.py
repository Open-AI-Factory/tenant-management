"""Authenticated actual budget routes; no reset, policy edit or admission unlock."""
from pathlib import Path
from errors import Error
from kueue_quota_control import KueueQuotaControl


def is_kueue_path(path):
    return path in ('/kueue-quota', '/kueue-quota/', '/api/kueue-quota') or path.startswith(
        ('/kueue-quota/', '/api/kueue-quota/'))


class KueueQuotaHTTP:
    def __init__(self, control: KueueQuotaControl, static_root: Path):
        self.control = control
        self.root = static_root

    def authorize(self, principal: dict) -> None:
        KueueQuotaControl.authorize(principal)

    def dispatch(self, principal: dict, method: str, path: str, body=None) -> tuple:
        self.authorize(principal)
        assets = {'/kueue-quota': ('kueue_quota.html', 'text/html; charset=utf-8'),
                  '/kueue-quota/': ('kueue_quota.html', 'text/html; charset=utf-8'),
                  '/kueue-quota/kueue_quota.js': ('kueue_quota.js', 'text/javascript; charset=utf-8'),
                  '/kueue-quota/kueue_quota.css': ('kueue_quota.css', 'text/css; charset=utf-8')}
        if method == 'GET' and path in assets:
            name, mime = assets[path]
            return 200, (self.root / name).read_bytes(), mime
        if method == 'GET' and path == '/api/kueue-quota/state':
            state = self.control.state() if self.control else {
                'enabled': False, 'tenants': {}, 'phase': 'Unavailable', 'reason': 'NotConfigured'}
            return 200, state, 'application/json; charset=utf-8'
        if method == 'POST' and path == '/api/kueue-quota/forecasts':
            if self.control is None: raise Error('실제 예산 제어가 설정되지 않았습니다.', 503, 'Disabled')
            return 200, self.control.receive(body, principal), 'application/json; charset=utf-8'
        raise Error('Not found', 404)
