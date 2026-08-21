"""Authenticated HTTP transport, shared by production and boundary tests."""
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from access import principal_from_user
from errors import Error
from quota_demo import QuotaDemo, is_demo_path
from kueue_quota_http import KueueQuotaHTTP, is_kueue_path
from tenant_budget_policy import strict_json


def grafana_authenticator(grafana, instance, tls_context, secret):
    def authenticate(headers):
        auth=headers.get('Authorization',''); cookie=SimpleCookie(); gh={}
        try: cookie.load(headers.get('Cookie',''))
        except Exception: pass
        if auth.startswith('Basic '): gh['Authorization']=auth; identity=auth
        elif 'grafana_session' in cookie:
            identity='grafana_session='+cookie['grafana_session'].value; gh['Cookie']=identity
        else: raise Error('Grafana 로그인이 필요합니다.',401)
        try:
            with urllib.request.urlopen(urllib.request.Request(grafana+'/api/user',headers=gh),context=tls_context,timeout=8) as response:
                user=json.load(response)
        except urllib.error.HTTPError as exc:
            raise Error('Grafana에 다시 로그인하세요.' if exc.code in (401,403) else '인증 서비스를 확인할 수 없습니다.',401 if exc.code in (401,403) else 503) from exc
        except (OSError,ValueError) as exc: raise Error('인증 서비스를 확인할 수 없습니다.',503) from exc
        if not isinstance(user,dict): raise Error('인증 응답이 올바르지 않습니다.',503)
        return principal_from_user(user,instance),hmac.new(secret,identity.encode(),hashlib.sha256).hexdigest()
    return authenticate


def make_handler(api, authenticate, origin, grafana, static_root, lock=None, kueue_control=None):
    lock=lock or threading.RLock()
    demo=QuotaDemo(static_root)
    budget=KueueQuotaHTTP(kueue_control,static_root)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass

        def send(self,status,data,ctype='application/json; charset=utf-8'):
            raw=json.dumps(data,ensure_ascii=False).encode() if ctype.startswith('application/json') else data
            self.send_response(status)
            for key,value in {'Content-Type':ctype,'Content-Length':str(len(raw)),'Cache-Control':'no-store',
                'X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin',
                'Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors "+grafana+"; base-uri 'none'; form-action 'self'"}.items(): self.send_header(key,value)
            self.end_headers(); self.wfile.write(raw)

        def dispatch(self):
            try:
                if self.command=='GET' and self.path=='/healthz': return self.send(200,{'ok':True})
                files={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}
                if self.command=='GET' and self.path in files:
                    name,mime=files[self.path]; return self.send(200,(static_root/name).read_bytes(),mime)
                principal,csrf=authenticate(self.headers)
                demo_path=is_demo_path(self.path)
                budget_path=is_kueue_path(self.path)
                if demo_path: demo.authorize(principal)
                if budget_path: budget.authorize(principal)
                if self.command=='GET' and self.path=='/api/session': return self.send(200,{'principal':principal,'csrf':csrf,'grafana':grafana})
                body=None
                if self.command!='GET':
                    self.connection.settimeout(5)
                    if self.headers.get('Origin')!=origin and not (not self.headers.get('Origin') and self.headers.get('Authorization','').startswith('Basic ')): raise Error('허용되지 않은 요청 출처입니다.',403)
                    if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),csrf): raise Error('요청 토큰이 만료되었습니다. 새로고침하세요.',403)
                    if self.headers.get('Content-Type','').split(';')[0]!='application/json': raise Error('JSON 요청이 필요합니다.',415)
                    try: length=int(self.headers.get('Content-Length','0'))
                    except ValueError: raise Error('요청 크기를 확인하세요.',413)
                    if not 0<length<=(32768 if demo_path or budget_path else 16384): raise Error('요청 크기를 확인하세요.',413)
                    if (demo_path or budget_path) and self.headers.get('Transfer-Encoding'): raise Error('Chunked body unsupported')
                    try:
                        if budget_path:
                            body=strict_json(self.rfile.read(length))
                        elif demo_path:
                            def invalid_constant(value): raise ValueError(value)
                            def unique_fields(pairs):
                                result={}
                                for key,value in pairs:
                                    if key in result: raise ValueError('Duplicate JSON field')
                                    result[key]=value
                                return result
                            body=json.loads(self.rfile.read(length),parse_constant=invalid_constant,object_pairs_hook=unique_fields)
                        else: body=json.loads(self.rfile.read(length))
                    except (ValueError,UnicodeError,RecursionError): raise Error('잘못된 JSON입니다.')
                    if not isinstance(body,dict): raise Error('JSON 객체가 필요합니다.')
                if demo_path:
                    status,result,mime=demo.dispatch(principal,self.command,self.path,body)
                    return self.send(status,result,mime)
                if budget_path:
                    with lock: status,result,mime=budget.dispatch(principal,self.command,self.path,body)
                    if self.command!='GET':
                        print(json.dumps({'event':'tenant.budget.forecast','actor':{k:principal[k] for k in
                            ('providerInstance','subjectKind','subjectId')},'tenantUID':body.get('tenantUID'),
                            'eventId':body.get('eventId'),'status':status}),flush=True)
                    return self.send(status,result,mime)
                with lock: status,result=api.dispatch(principal,self.command,self.path,body)
                if self.command!='GET':
                    print(json.dumps({'event':'tenant.api.mutation','method':self.command,'path':self.path.split('?')[0],
                        'actor':{k:principal[k] for k in ('providerInstance','subjectKind','subjectId')},'status':status}),flush=True)
                return self.send(status,result)
            except Error as exc: self.send(exc.status,{'error':str(exc),'code':exc.code})
            except TimeoutError: self.send(408,{'error':'Request timed out'})
            except Exception as exc:
                print('Request error: '+type(exc).__name__,flush=True)
                self.send(500,{'error':'서버 처리 중 오류가 발생했습니다.'})
        do_GET=dispatch
        do_POST=dispatch
        do_PUT=dispatch
    return Handler
