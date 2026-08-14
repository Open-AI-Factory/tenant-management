"""Bounded real-GPU smoke workloads; queue, namespace and claim are server-derived."""
import hashlib,json,re,secrets

OWNER='tenancy.gpu.platform.io/policy-uid'
MANAGED='tenancy.gpu.platform.io/console'
ACTOR='system:serviceaccount:monitoring:tenant-admin'
IMAGE='docker.io/pytorch/pytorch@sha256:7b324d212a4450795b49edba9949b7cdc72429148a64e974334bfe5774d51385'
class WorkloadError(Exception):
    def __init__(self,message,status=400):super().__init__(message);self.status=status

def template_spec(pool):
    kind='mig' if pool['mode']=='mig' else 'gpu'
    expr='device.driver == "gpu.nvidia.com" && device.attributes["gpu.nvidia.com"].type == '+json.dumps(kind)+' && device.attributes["gpu.nvidia.com"].productName == '+json.dumps(pool['model'])
    if kind=='mig':expr+=' && device.attributes["gpu.nvidia.com"].profile == '+json.dumps(pool['profile'])
    return {'metadata':{},'spec':{'devices':{'requests':[{'name':'gpu','exactly':{'deviceClassName':'mig.nvidia.com' if kind=='mig' else 'gpu.nvidia.com','allocationMode':'ExactCount','count':1,'selectors':[{'cel':{'expression':expr}}]}}]}}}

def binding(p):
    return {'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicyBinding','metadata':{'name':'tenant-workload-'+p['metadata']['name'],'labels':{MANAGED:'true',OWNER:p['metadata']['uid']}},'spec':{'policyName':'tenant-workload-guard','validationActions':['Deny'],'paramRef':{'name':p['metadata']['name'],'parameterNotFoundAction':'Deny'},'matchResources':{'namespaceSelector':{'matchLabels':{OWNER:p['metadata']['uid']}}}}}

def check_ready(k,p):
    st=p.get('status',{});s=p['spec']
    if s['mode']!='gpu-test' or st.get('phase')!='Ready' or not st.get('workloadsEnabled') or st.get('observedGeneration')!=p['metadata']['generation']:raise WorkloadError('GPU 테스트 실행을 활성화하고 적용 완료를 기다리세요.',409)
    ns=k.call('/api/v1/namespaces/'+s['namespaceName'])
    if ns['metadata']['uid']!=st.get('namespaceUID') or not any(x.get('uid')==st.get('tenantUID') and x.get('kind')=='Tenant' for x in ns['metadata'].get('ownerReferences',[])):raise WorkloadError('테넌트 소유권이 변경되었습니다.',409)
    q=k.call('/apis/scheduling.run.ai/v2/queues/'+st['queue'])
    expected={key:{'quota':v['guaranteed'],'limit':v['maximum'],'overQuotaWeight':1} for key,v in s['resources'].items()}
    if q['metadata'].get('labels',{}).get(OWNER)!=p['metadata']['uid'] or q['spec'].get('resources')!=expected:raise WorkloadError('KAI 큐 설정이 정책과 일치하지 않습니다.',409)
    t=k.call('/apis/resource.k8s.io/v1/namespaces/'+s['namespaceName']+'/resourceclaimtemplates/tenant-gpu')
    if t['metadata']['uid']!=st.get('templateUID') or t['spec']!=template_spec(s['pool']):raise WorkloadError('GPU 요청 템플릿이 정책과 일치하지 않습니다.',409)
    if s['resources']['memory']['maximum']<2000:raise WorkloadError('GPU 테스트에는 메모리 쿼터 2GB 이상이 필요합니다.')

def pod_manifest(p,duration,name=None):
    name=name or 'tjob-'+secrets.token_hex(6)
    code="""import torch,time,json
assert torch.cuda.is_available(), 'CUDA unavailable'
assert torch.cuda.device_count()==1, 'Expected exactly one device'
p=torch.cuda.get_device_properties(0)
a=torch.ones((256,256),device='cuda'); b=a@a; torch.cuda.synchronize()
print(json.dumps({'event':'GPU_STARTED','device':p.name,'uuid':str(getattr(p,'uuid','unknown')),'cudaDevices':torch.cuda.device_count(),'result':float(b[0,0])}),flush=True)
time.sleep(DURATION)
print(json.dumps({'event':'GPU_COMPLETED'}),flush=True)
""".replace('DURATION',str(duration))
    return {'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':p['spec']['namespaceName'],'labels':{MANAGED:'true',OWNER:p['metadata']['uid'],'tenancy.gpu.platform.io/job':'gpu-smoke','kai.scheduler/queue':p['status']['queue']}},'spec':{'schedulerName':'kai-scheduler','restartPolicy':'Never','automountServiceAccountToken':False,'activeDeadlineSeconds':900,'terminationGracePeriodSeconds':5,
        'securityContext':{'runAsNonRoot':True,'runAsUser':65532,'runAsGroup':65532,'seccompProfile':{'type':'RuntimeDefault'}},
        'affinity':{'nodeAffinity':{'requiredDuringSchedulingIgnoredDuringExecution':{'nodeSelectorTerms':[{'matchExpressions':[{'key':'kubernetes.io/hostname','operator':'In','values':p['spec']['pool']['nodes']}]}]}}},
        'resourceClaims':[{'name':'gpu','resourceClaimTemplateName':'tenant-gpu'}],
        'containers':[{'name':'gpu-test','image':IMAGE,'imagePullPolicy':'IfNotPresent','command':['python','-u','-c',code],'env':[{'name':'NVIDIA_VISIBLE_DEVICES','value':'void'}],'securityContext':{'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},'resources':{'requests':{'cpu':'500m','memory':'1Gi'},'limits':{'cpu':'1','memory':'1Gi'},'claims':[{'name':'gpu'}]}}]}}

def submit(k,p,body):
    if set(body)!={'durationSeconds'} or type(body.get('durationSeconds')) is not int or not 30<=body['durationSeconds']<=180:raise WorkloadError('실행 시간은 30~180초 정수만 허용합니다.')
    check_ready(k,p)
    pods=k.call('/api/v1/namespaces/'+p['spec']['namespaceName']+'/pods')['items']
    if len(pods)>=8:raise WorkloadError('테스트 작업은 최대 8개입니다. 완료 작업을 정리하세요.',409)
    return k.call('/api/v1/namespaces/'+p['spec']['namespaceName']+'/pods','POST',pod_manifest(p,body['durationSeconds']))

def owned_pod(k,p,name):
    if not re.fullmatch(r'tjob-[a-f0-9]{12}',name):raise WorkloadError('잘못된 작업 ID입니다.')
    pod=k.call('/api/v1/namespaces/'+p['spec']['namespaceName']+'/pods/'+name)
    if pod['metadata'].get('labels',{}).get(OWNER)!=p['metadata']['uid']:raise WorkloadError('다른 테넌트의 작업입니다.',403)
    return pod

def jobs(k,p):
    ns=p['spec']['namespaceName'];out=[]
    claims=k.call('/apis/resource.k8s.io/v1/namespaces/'+ns+'/resourceclaims')['items']
    events=k.call('/api/v1/namespaces/'+ns+'/events')['items']
    for pod in k.call('/api/v1/namespaces/'+ns+'/pods')['items']:
        m=pod['metadata'];s=pod.get('status',{})
        if m.get('labels',{}).get(OWNER)!=p['metadata']['uid']:continue
        allocated=[r for c in claims if any(o.get('uid')==m['uid'] for o in c['metadata'].get('ownerReferences',[])) for r in c.get('status',{}).get('allocation',{}).get('devices',{}).get('results',[])]
        es=sorted([e for e in events if e.get('involvedObject',{}).get('uid')==m['uid']],key=lambda e:e.get('lastTimestamp') or e['metadata']['creationTimestamp'])
        message=next((x.get('message','') for x in s.get('conditions',[]) if x.get('type')=='PodScheduled' and x.get('status')=='False'),'')
        out.append({'name':m['name'],'uid':m['uid'],'phase':s.get('phase','Pending'),'deleting':bool(m.get('deletionTimestamp')),'node':pod['spec'].get('nodeName',''),'created':m['creationTimestamp'],'queue':m['labels'].get('kai.scheduler/queue'),'allocation':allocated,'message':message or (es[-1].get('message','') if es else ''),'events':[{'reason':e.get('reason'),'message':e.get('message')} for e in es[-5:]]})
    return out
