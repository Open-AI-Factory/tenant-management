"""Administrator-only demo routes. No TenantAPI or Kubernetes mutation path."""
import json
from errors import Error
from forecast_quota import ForecastError
from quota_simulator import Simulator


def is_demo_path(path):
    return path in ('/quota-demo', '/quota-demo/', '/api/quota-demo') or path.startswith(('/quota-demo/', '/api/quota-demo/'))


class QuotaDemo:
    def __init__(self, root):
        self.root = root
        self.sim = Simulator(json.loads((root / 'quota_demo_config.json').read_text(encoding='utf-8')))

    def authorize(self, principal):
        if principal.get('platformAdmin') is not True:
            raise Error('예측 쿼터 데모는 플랫폼 관리자만 사용할 수 있습니다.', 403)

    def dispatch(self, principal, method, path, body):
        self.authorize(principal)
        assets = {'/quota-demo': ('quota_simulator.html', 'text/html; charset=utf-8'),
                  '/quota-demo/': ('quota_simulator.html', 'text/html; charset=utf-8'),
                  '/quota-demo/quota_simulator.js': ('quota_simulator.js', 'text/javascript; charset=utf-8'),
                  '/quota-demo/quota_simulator.css': ('quota_simulator.css', 'text/css; charset=utf-8')}
        if method == 'GET' and path in assets:
            name, mime = assets[path]
            data = (self.root / name).read_bytes()
            if name.endswith('.html'):
                text = data.decode('utf-8').replace('href="/quota_simulator.css"', 'href="/quota-demo/quota_simulator.css"')
                text = text.replace('src="/quota_simulator.js"', 'src="/quota-demo/quota_simulator.js"')
                text = text.replace('<body>', '<body data-api-base="/api/quota-demo">')
                text = text.replace('LOCAL SIMULATION', 'WEB SIMULATION')
                text = text.replace('<header>', '<header><a href="/">← 테넌트 관리로 돌아가기</a>')
                text = text.replace('모의 예산만 변경합니다.', '관리자들이 공유하는 모의 실험입니다. ‘처음부터’는 공유 기록을 초기화합니다. 모의 예산만 변경합니다.')
                text = text.replace('별도 프로세스에서도 <code>dummy_profiler.py</code>로 데이터를 보낼 수 있습니다.', '이 페이지의 예측 전송 버튼으로 외부 모듈 입력을 흉내 냅니다.')
                data = text.encode('utf-8')
            return 200, data, mime
        with self.sim.lock:
            if method == 'GET' and path == '/api/quota-demo/state':
                return 200, self.sim.state(), 'application/json; charset=utf-8'
            if method == 'POST' and path.startswith('/api/quota-demo/'):
                try:
                    result = self.sim.post('/api/' + path[len('/api/quota-demo/'):], body)
                except ForecastError as exc:
                    raise Error(str(exc), exc.status) from exc
                return 200, result, 'application/json; charset=utf-8'
        raise Error('Not found', 404)
