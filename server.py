# -*- coding: utf-8 -*-
"""Authenticated configuration-test console. All tenant workloads remain locked."""
import base64,hashlib,hmac,json,os,re,secrets,ssl,threading,time,urllib.request,urllib.error,urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import workloads
from errors import Error

MANAGED='tenancy.gpu.platform.io/console'
OWNER='tenancy.gpu.platform.io/policy-uid'
BASE='/apis/tenancy.gpu.platform.io/v1alpha1/tenantresourcepolicies'
TENANTS='/apis/capsule.clastix.io/v1beta2/tenants'
QUEUES='/apis/scheduling.run.ai/v2/queues'
SECRET=secrets.token_bytes(32)
LOCK=threading.RLock()

class Kube:
    def __init__(self):
        self.root=Path('/var/run/secrets/kubernetes.io/serviceaccount')
        self.ctx=ssl.create_default_context(cafile=str(self.root/'ca.crt'))
        self.url='https://'+os.environ['KUBERNETES_SERVICE_HOST']+':'+os.environ.get('KUBERNETES_SERVICE_PORT','443')
    def call(self,path,method='GET',body=None,optional=False):
        headers={'Authorization':'Bearer '+(self.root/'token').read_text().strip(),'Content-Type':'application/json'}
        req=urllib.request.Request(self.url+path,method=method,headers=headers,data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req,context=self.ctx,timeout=12) as r:
                return json.load(r) if 'application/json' in r.headers.get('Content-Type','') else r.read().decode()
        except urllib.error.HTTPError as e:
            if optional and e.code==404:return None
            try:msg=json.loads(e.read()).get('message','Kubernetes request failed')
            except Exception:msg='Kubernetes request failed'
            raise Error(msg,e.code)

def pool_inventory(kube):
    groups={}; newest={}
    slices=kube.call('/apis/resource.k8s.io/v1/resourceslices')['items']
    for s in slices:
        sp=s['spec'];key=(sp['driver'],sp['pool']['name']);newest[key]=max(newest.get(key,-1),sp['pool']['generation'])
    for s in slices:
        sp=s['spec']
        if sp['driver']!='gpu.nvidia.com' or sp['pool']['generation']!=newest[(sp['driver'],sp['pool']['name'])]:continue
        for d in sp.get('devices',[]):
            a=d.get('attributes',{});kind=a.get('type',{}).get('string');model=a.get('productName',{}).get('string')
            if not model or kind not in ['gpu','mig']:continue
            mode='whole' if kind=='gpu' else 'mig';profile=a.get('profile',{}).get('string','')
            if mode=='mig' and profile!='2g.48gb':continue
            key=mode+'-'+hashlib.sha256((model if mode=='whole' else model+'|'+profile).encode()).hexdigest()[:10]
            g=groups.setdefault(key,{'key':key,'model':model,'mode':mode,'profile':profile,'devices':set(),'nodes':set()})
            g['devices'].add((sp['pool']['name'],d['name']));g['nodes'].add(sp.get('nodeName',sp['pool']['name']))
    return [dict(key=g['key'],model=g['model'],mode=g['mode'],profile=g['profile'],capacity=len(g['devices']),nodes=sorted(g['nodes'])) for g in groups.values()]

def validate(body,pools):
    allowed={'name','displayName','contact','pool','gpu','cpu','memoryGB','resourceVersion','workloadsEnabled','additionalNamespaceNames'}
    if set(body)-allowed:raise Error('지원하지 않는 필드가 있습니다.')
    name=body.get('name','')
    if not isinstance(name,str) or not re.fullmatch(r'[a-z][a-z0-9-]{1,28}[a-z0-9]',name):raise Error('이름은 영문 소문자로 시작하는 3~30자의 영문·숫자·하이픈이어야 합니다.')
    if name.startswith(('kube-','capsule-','default','ai-factory')):raise Error('시스템 이름은 사용할 수 없습니다.')
    for key,maxlen in [('displayName',80),('contact',120)]:
        if not isinstance(body.get(key,''),str) or len(body.get(key,''))>maxlen:raise Error(key+' 길이를 확인하세요.')
    pool=next((p for p in pools if p['key']==body.get('pool')),None)
    if not pool:raise Error('사용 가능한 GPU 자원풀을 선택하세요.')
    if type(body.get('workloadsEnabled',False)) is not bool:raise Error('실행 허용 값이 잘못되었습니다.')
    for key,lo,hi in [('gpu',1,pool['capacity']),('cpu',1,32),('memoryGB',1,128)]:
        if type(body.get(key)) is not int or not lo<=body[key]<=hi:raise Error(f'{key}: {lo}~{hi} 사이 정수를 입력하세요.')
    result={'tenantName':name,'namespaceName':'tenant-'+name,'displayName':body.get('displayName','').strip() or name,
        'contact':body.get('contact','').strip(),'pool':pool,'mode':'gpu-test' if body.get('workloadsEnabled') else 'configuration-test',
        'resources':{k:{'guaranteed':v,'maximum':v} for k,v in [('gpu',body['gpu']),('cpu',body['cpu']*1000),('memory',body['memoryGB']*1000)]}}
    if 'additionalNamespaceNames' in body:
        result['additionalNamespaceNames']=body['additionalNamespaceNames']
    from namespaces import policy_namespaces
    policy_namespaces({'spec':result})
    return result

def metadata(name,uid,namespace=None):
    d={'name':name,'labels':{MANAGED:'true',OWNER:uid}}
    if namespace:d['namespace']=namespace
    return d

def ensure(k,path,desired,uid,update=False):
    name=desired['metadata']['name'];old=k.call(path+'/'+name,optional=True)
    if not old:return k.call(path,'POST',desired)
    if old['metadata'].get('labels',{}).get(OWNER)!=uid or old['metadata'].get('deletionTimestamp'):raise Error('기존 객체를 인수할 수 없습니다: '+name,409)
    if update and old.get('spec')!=desired.get('spec'):
        old['spec']=desired['spec'];return k.call(path+'/'+name,'PUT',old)
    return old

def reconcile(k,p):
    from namespaces import reconcile_namespaces, policy_namespaces
    spec=p['spec'];uid=p['metadata']['uid'];name=spec['tenantName'];ns=spec['namespaceName']
    t=ensure(k,TENANTS,{'apiVersion':'capsule.clastix.io/v1beta2','kind':'Tenant','metadata':metadata(name,uid),'spec':{'namespaceOptions':{'quota':1},'owners':[]}},uid)
    pods=[];claims=[]
    for space in policy_namespaces(p):
        if k.call('/api/v1/namespaces/'+space,optional=True):
            pods.extend(k.call('/api/v1/namespaces/'+space+'/pods')['items'])
            claims.extend(k.call('/apis/resource.k8s.io/v1/namespaces/'+space+'/resourceclaims')['items'])
    queue='tenant-'+name+'-'+hashlib.sha256(uid.encode()).hexdigest()[:10]
    qspec={'displayName':spec['displayName'],'resources':{key:{'quota':v['guaranteed'],'limit':v['maximum'],'overQuotaWeight':1} for key,v in spec['resources'].items()}}
    currentq=k.call(QUEUES+'/'+queue,optional=True)
    pinned=p.get('status',{}).get('queueUID')
    if pinned and (p['status'].get('queue')!=queue or not currentq or currentq['metadata']['uid']!=pinned):
        raise Error('KAI Queue UID가 변경되었습니다. 기존 큐를 인수하지 않습니다.',409)
    if (pods or claims) and (not currentq or currentq.get('spec')!=qspec or spec['mode']!='gpu-test'):raise Error('작업 또는 ResourceClaim이 남아 있어 설정 변경을 중지했습니다.',409)
    q=ensure(k,QUEUES,{'apiVersion':'scheduling.run.ai/v2','kind':'Queue','metadata':metadata(queue,uid),'spec':qspec},uid,True)
    ensure(k,'/apis/admissionregistration.k8s.io/v1/validatingadmissionpolicybindings',workloads.binding(p),uid,True)
    from admission import queue_scope_binding
    ensure(k,'/apis/admissionregistration.k8s.io/v1/validatingadmissionpolicybindings',queue_scope_binding(p,queue),uid,True)
    bindings=reconcile_namespaces(k,p,t)
    enabled=spec['mode']=='gpu-test'
    # Create the fail-closed admission binding before permitting any Pod/Claim objects.
    hard={'pods':'8' if enabled else '0','count/resourceclaims.resource.k8s.io':'8' if enabled else '0'}
    for space in bindings:
        from network_policy import namespace_network_policy
        # Fixed CUDA profile needs no ingress. DNS peers are explicit and operator-configured.
        dns=json.loads(os.environ.get('TENANT_DNS_PEERS','[]'))
        network=namespace_network_policy(space,dns,[])
        network['metadata']['labels']=metadata('tenant-boundary',uid)['labels']
        ensure(k,'/apis/networking.k8s.io/v1/namespaces/'+space+'/networkpolicies',network,uid,True)
        rq={'apiVersion':'v1','kind':'ResourceQuota','metadata':metadata('configuration-test-lock',uid,space),'spec':{'hard':hard}}
        ensure(k,'/api/v1/namespaces/'+space+'/resourcequotas',rq,uid,True)
    return {'phase':'Ready','message':'GPU 테스트 제출 가능 · 테넌트 큐/자원풀 검증 적용' if enabled else '구성 적용 완료 · GPU 작업 실행 잠금','tenantUID':t['metadata']['uid'],'namespaceUID':bindings[ns]['uid'],'queue':q['metadata']['name'],'queueUID':q['metadata']['uid'],'namespaceBindings':bindings,'templateUID':bindings[ns]['templateUID'],'observedGeneration':p['metadata']['generation'],'workloadsEnabled':enabled}

def sync_one(k,p):
    try:status=reconcile(k,p)
    except Exception as e:
        status=dict(p.get('status',{}))
        status.update(phase='Error',message=str(e)[:600],observedGeneration=p['metadata']['generation'],workloadsEnabled=False)
    if p.get('status')==status:return
    current=k.call(BASE+'/'+p['metadata']['name'])
    if current['metadata']['generation']!=p['metadata']['generation']:return
    current['status']=status
    k.call(BASE+'/'+p['metadata']['name']+'/status','PUT',current)

def public(p):
    return {'name':p['metadata']['name'],'resourceVersion':p['metadata']['resourceVersion'],'generation':p['metadata']['generation'],'spec':p['spec'],'status':p.get('status',{}),'updatedBy':p['metadata'].get('annotations',{}).get('tenancy.gpu.platform.io/updated-by','')}


def main():
    from runtime import main as run
    run()

if __name__ == '__main__': main()
