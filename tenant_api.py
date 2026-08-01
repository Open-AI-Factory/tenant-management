"""HTTP-independent application boundary; every request rechecks portal membership."""
import base64
import copy
import hashlib
import hmac
import json
import re
import urllib.parse
from errors import Error
from access import authorize, validate_bindings, IDENTITY
from kubeutil import read, listing, BASE, TENANTS, MANAGED
from tenant_inventory import list_tenants, filter_tenants
from tenant_usage import observe_usage
from namespaces import policy_namespaces, assert_policy_editable
from context import resolve_context
import executions as runs


class TenantAPI:
    def __init__(self, kube, cursor_secret, mutations_enabled=True):
        self.k = kube; self.secret = cursor_secret; self.mutations_enabled=mutations_enabled

    def state(self, principal):
        tenants = listing(self.k, TENANTS)
        policies = listing(self.k, BASE)
        namespaces = listing(self.k, '/api/v1/namespaces')
        states = {}
        for p in policies:
            try:
                for ns in policy_namespaces(p): resolve_context(self.k, p, ns)
                states[p['metadata']['uid']] = 'Ready'
            except Error as exc:
                states[p['metadata']['uid']] = 'Unavailable' if exc.status == 503 else exc.code or 'IdentityMismatch'
        visible = filter_tenants(principal, list_tenants(dict(tenants=tenants, policies=policies,
            namespaces=namespaces, policyStates=states)), runs.access_bindings(self.k))
        from server import public, pool_inventory
        for row in visible:
            row['usage'] = observe_usage(self.k, row)
            row['policy'] = public(row['policy']) if row['policy'] else None
            row['vram'] = {'enforced': False, 'source': 'observation'}
            row['capabilities']['add_namespace'] &= bool(row['policy'])
            row['capabilities']['configure'] &= bool(row['policy'])
            if not self.mutations_enabled:
                row['capabilities'] = {action: False for action in row['capabilities']}
        return {'tenants': visible, 'pools': pool_inventory(self.k) if principal.get('platformAdmin') else [],
                'platformAdmin': principal.get('platformAdmin') is True,
                'mutationsEnabled': self.mutations_enabled}

    def tenant(self, principal, name, action='read', hide=False):
        tenant = read(self.k, TENANTS+'/'+name, optional=True)
        if tenant is None: raise Error('테넌트를 찾을 수 없습니다.', 404)
        try: authorize(principal, tenant['metadata']['uid'], action, runs.access_bindings(self.k))
        except Error as exc:
            if hide and exc.status == 403: raise Error('실행을 찾을 수 없습니다.',404) from exc
            raise
        if tenant['metadata'].get('deletionTimestamp'): raise Error('Tenant를 삭제 중입니다.',409)
        return tenant

    def policy(self, tenant):
        found = [p for p in listing(self.k, BASE) if p['spec']['tenantName'] == tenant['metadata']['name']]
        if len(found) != 1: raise Error('고유한 실행 정책이 없습니다.',409)
        p=found[0]
        if p['metadata'].get('labels',{}).get(MANAGED) != 'true' or p.get('status',{}).get('tenantUID') != tenant['metadata']['uid']:
            raise Error('정책 소유권을 확인할 수 없습니다.',409)
        return p

    def scope(self, tenant, namespace):
        if not isinstance(namespace,str) or not re.fullmatch(r'[a-z0-9-]{1,63}',namespace):
            raise Error('namespaceName이 필요합니다.')
        ns=read(self.k,'/api/v1/namespaces/'+namespace,optional=True)
        tm=tenant['metadata']; nm=ns['metadata'] if ns else {}
        if (not ns or nm.get('deletionTimestamp') or nm.get('labels',{}).get('capsule.clastix.io/tenant') != tm['name'] or
            not any(o.get('kind')=='Tenant' and o.get('uid')==tm['uid'] and o.get('name')==tm['name'] for o in nm.get('ownerReferences',[]))):
            raise Error('실행 공간을 찾을 수 없습니다.',404)
        return dict(tenantName=tm['name'],tenantUID=tm['uid'],namespaceName=namespace,namespaceUID=nm['uid'])

    def tenant_records(self, tenant_uid):
        return [r for r in (runs.decode(cm) for cm in runs.records(self.k)) if r['context']['tenantUID']==tenant_uid]

    def cursor(self, scope, last):
        raw=base64.urlsafe_b64encode(runs.compact([scope,last]).encode()).decode().rstrip('=')
        return raw+'.'+hmac.new(self.secret,raw.encode(),hashlib.sha256).hexdigest()

    def page(self, principal, ctx, cursor):
        scope=[*[principal[k] for k in IDENTITY],ctx['tenantUID'],ctx['namespaceUID']]; last=''
        if cursor:
            try:
                raw,sig=cursor.split('.')
                if not hmac.compare_digest(sig,hmac.new(self.secret,raw.encode(),hashlib.sha256).hexdigest()): raise ValueError()
                saved,last=json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                if saved != scope or not isinstance(last,str): raise ValueError()
            except (ValueError,TypeError,UnicodeError): raise Error('목록 커서가 만료되었거나 올바르지 않습니다.',409)
        rows=sorted([r for r in self.tenant_records(ctx['tenantUID']) if r['context']['namespaceUID']==ctx['namespaceUID'] and r['recordName']>last],key=lambda r:r['recordName'])
        page=rows[:50]
        for r in page:
            try: authorize(principal,ctx['tenantUID'],'cancel',runs.access_bindings(self.k),r['actor']); r['canCancel']=self.mutations_enabled and bool(r['pod'].get('uid'))
            except Error as exc:
                if exc.status != 403: raise
                r['canCancel']=False
        return {'jobs':page,'nextCursor':self.cursor(scope,page[-1]['recordName']) if len(rows)>50 else None}

    def members(self, principal, tenant, method, body):
        uid=tenant['metadata']['uid']; authorize(principal,uid,'members',runs.access_bindings(self.k))
        cm=read(self.k,runs.ACCESS,optional=True)
        all_bindings=runs.access_bindings(self.k)
        if method=='PUT':
            if set(body)!={'resourceVersion','bindings'}: raise Error('구성원 요청 필드를 확인하세요.')
            if body['resourceVersion'] != (cm['metadata']['resourceVersion'] if cm else None): raise Error('구성원 설정이 변경되었습니다.',409)
            chosen=validate_bindings(body['bindings'])
            if any(b['tenantUID']!=uid or b['providerInstance']!=principal['providerInstance'] for b in chosen): raise Error('선택한 Tenant와 인증 제공자의 구성원만 설정하세요.')
            all_bindings=validate_bindings([b for b in all_bindings if b['tenantUID']!=uid]+chosen)
            data={'bindings.json':runs.compact(all_bindings)}
            if cm:
                cm['data']=data; cm=self.k.call(runs.ACCESS,'PUT',body=cm)
            else: cm=self.k.call(runs.CMS,'POST',body={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'tenant-access-v1','namespace':'monitoring'},'data':data})
        return {'resourceVersion':cm['metadata']['resourceVersion'] if cm else None,'bindings':[b for b in all_bindings if b['tenantUID']==uid]}

    def configure(self, principal, method, name, body):
        if principal.get('platformAdmin') is not True: raise Error('플랫폼 관리자만 설정할 수 있습니다.',403)
        from server import validate, public, pool_inventory
        p=None
        if method=='PUT':
            tenant=self.tenant(principal,name,'configure'); p=self.policy(tenant)
            if body.get('resourceVersion')!=p['metadata']['resourceVersion']: raise Error('정책이 변경되었습니다.',409)
            body=dict(body); body.setdefault('additionalNamespaceNames',p['spec'].get('additionalNamespaceNames',[]))
        spec=validate(body,pool_inventory(self.k)); policy_name=spec['tenantName']
        if p:
            if name!=policy_name or spec['pool']['key']!=p['spec']['pool']['key'] or spec.get('additionalNamespaceNames',[])!=p['spec'].get('additionalNamespaceNames',[]):
                raise Error('이름·자원풀 변경 또는 Namespace 삭제는 지원하지 않습니다.',409)
            assert_policy_editable(self.k,p,self.tenant_records(tenant['metadata']['uid']))
            p['spec']=spec
        else:
            if spec.get('additionalNamespaceNames'): raise Error('생성 후 Namespace를 추가하세요.')
            if read(self.k,TENANTS+'/'+policy_name,optional=True) or read(self.k,'/api/v1/namespaces/'+spec['namespaceName'],optional=True): raise Error('같은 이름의 Tenant 또는 Namespace가 있습니다.',409)
            p={'apiVersion':'tenancy.gpu.platform.io/v1alpha1','kind':'TenantResourcePolicy','metadata':{'name':policy_name,'labels':{MANAGED:'true'}},'spec':spec}
        p['metadata'].setdefault('annotations',{}).update({'tenancy.gpu.platform.io/updated-by':principal.get('login',''),'tenancy.gpu.platform.io/updated-at':runs.now()})
        saved=self.k.call(BASE+('/'+name if method=='PUT' else ''),method,body=p)
        return (200 if method=='PUT' else 201),public(saved)

    def dispatch(self, principal, method, path, body=None):
        if method != 'GET' and not self.mutations_enabled: raise Error('배포 검증 중입니다. 변경과 제출이 잠겨 있습니다.',503)
        parsed=urllib.parse.urlsplit(path); query=urllib.parse.parse_qs(parsed.query,keep_blank_values=True)
        if set(query)-{'namespaceName','cursor'} or any(len(v)!=1 for v in query.values()): raise Error('지원하지 않는 조회 필드입니다.')
        path=parsed.path; body={} if body is None else body
        if method=='GET' and path=='/api/state': return 200,self.state(principal)
        if method=='POST' and path=='/api/tenants': return self.configure(principal,method,None,body)
        match=re.fullmatch(r'/api/tenants/([a-z0-9-]+)(?:/(members|namespaces|jobs)(?:/([a-z0-9-]{1,63})(?:/(cancel|logs))?)?)?',path)
        if not match: raise Error('Not found',404)
        name,section,request_id,op=match.groups()
        if method=='PUT' and section is None: return self.configure(principal,method,name,body)
        tenant=self.tenant(principal,name,hide=section=='jobs')
        if section=='members' and not request_id and method in ('GET','PUT'): return 200,self.members(principal,tenant,method,body)
        if section=='namespaces' and not request_id and method=='POST':
            authorize(principal,tenant['metadata']['uid'],'add_namespace',runs.access_bindings(self.k))
            p=self.policy(tenant)
            if set(body)!={'purpose','resourceVersion'} or not isinstance(body.get('purpose'),str) or not re.fullmatch('[a-z0-9]{1,10}',body['purpose']): raise Error('용도명은 영문 소문자·숫자 1~10자입니다.')
            if body['resourceVersion']!=p['metadata']['resourceVersion']: raise Error('정책이 변경되었습니다.',409)
            assert_policy_editable(self.k,p,self.tenant_records(tenant['metadata']['uid']))
            namespace=p['spec']['namespaceName']+'-'+body['purpose']
            if read(self.k,'/api/v1/namespaces/'+namespace,optional=True): raise Error('기존 Namespace는 인수하지 않습니다.',409)
            p['spec'].setdefault('additionalNamespaceNames',[]).append(namespace); policy_namespaces(p)
            from server import public
            return 201,public(self.k.call(BASE+'/'+p['metadata']['name'],'PUT',body=p))
        if section!='jobs': raise Error('Not found',404)
        namespace=body.get('namespaceName') if method=='POST' else query.get('namespaceName',[None])[0]
        if method=='POST' and not request_id:
            authorize(principal,tenant['metadata']['uid'],'submit',runs.access_bindings(self.k))
            if set(body)!={'durationSeconds'}:
                scope=self.scope(tenant,namespace)
                replay=runs.replay_execution(self.k,scope,principal,body)
                if replay is not None: return 200,replay
            p=self.policy(tenant)
            if principal.get('platformAdmin') is True and set(body)=={'durationSeconds'}: namespace=p['spec']['namespaceName']
            self.scope(tenant,namespace)
            ctx=resolve_context(self.k,p,namespace)
            record,created=runs.submit_execution(self.k,ctx,principal,body)
            return (201 if created else 200),record
        ctx=self.scope(tenant,namespace)
        if method=='GET' and not request_id: return 200,self.page(principal,ctx,query.get('cursor',[None])[0])
        if request_id:
            record=runs.get_execution(self.k,ctx,principal,request_id)
            if method=='GET' and not op: return 200,record
            if method=='POST' and op=='cancel':
                if set(body)!={'namespaceName','uid'}: raise Error('취소 요청 필드를 확인하세요.')
                return 200,runs.cancel_execution(self.k,ctx,principal,request_id,body['uid'])
            if method=='GET' and op=='logs':
                podpath='/api/v1/namespaces/'+ctx['namespaceName']+'/pods/'+record['pod']['name']
                pod=read(self.k,podpath,optional=True)
                if pod is None: raise Error('작업이 정리되어 로그를 조회할 수 없습니다.',404)
                if pod['metadata']['uid']!=record['pod']['uid']: raise Error('Pod UID가 변경되었습니다.',409)
                return 200,{'logs':read(self.k,podpath+'/log?tailLines=30&limitBytes=8000')}
        raise Error('Not found',404)
