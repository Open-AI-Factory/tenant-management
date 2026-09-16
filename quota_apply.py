"""Optional existing-API bridge; not wired to the local simulation or live runtime.

The caller must supply an authenticated principal, serialize policy operations,
and own cluster-wide admission/capacity coordination before enabling this bridge.
PolicyUpdated means a policy write only. Reconcile and read-back are separate.
"""
import re
from errors import Error
from kubeutil import read, QUEUES, OWNER
from namespaces import policy_namespaces


def apply_quota(api, principal, tenant_name, gpu, expected):
    try:
        if not api.mutations_enabled:raise Error('Mutations disabled',503)
        if principal.get('platformAdmin') is not True:raise Error('Platform administrator required',403)
        if (not isinstance(tenant_name,str) or not re.fullmatch('[a-z0-9-]{1,63}',tenant_name) or
            type(gpu) is not int or not 1<=gpu<=1000000 or not isinstance(expected,dict) or
            set(expected)!={'tenantUID','policyUID','queueUID','resourceVersion','gpu'} or
            any(not isinstance(expected[k],str) or not expected[k] for k in ('tenantUID','policyUID','queueUID','resourceVersion')) or
            type(expected['gpu']) is not int or expected['gpu']<1):raise Error('Invalid proposal preconditions',400)
        tenant=api.tenant(principal,tenant_name,'configure');p=api.policy(tenant)
        pm,s,status=p['metadata'],p['spec'],p.get('status',{})
        actual=dict(tenantUID=tenant['metadata']['uid'],policyUID=pm['uid'],queueUID=status.get('queueUID'),
                    resourceVersion=pm['resourceVersion'],gpu=s['resources']['gpu']['guaranteed'])
        if actual!=expected or pm.get('deletionTimestamp'):raise Error('Proposal is no longer current',409)
        q=read(api.k,QUEUES+'/'+status.get('queue',''))
        expected_resources={k:dict(quota=v['guaranteed'],limit=v['maximum'],overQuotaWeight=1) for k,v in s['resources'].items()}
        if (q['metadata']['uid']!=expected['queueUID'] or q['metadata'].get('deletionTimestamp') or
            q['metadata'].get('labels',{}).get(OWNER)!=pm['uid'] or q['spec'].get('resources')!=expected_resources):
            raise Error('Queue identity or quota changed',409)
        for name in policy_namespaces(p):
            ns=read(api.k,'/api/v1/namespaces/'+name);nm=ns['metadata']
            if (nm['uid']!=status.get('namespaceBindings',{}).get(name,{}).get('uid') or nm.get('deletionTimestamp') or
                nm.get('labels',{}).get(OWNER)!=pm['uid'] or not any(o.get('uid')==expected['tenantUID'] and
                o.get('kind')=='Tenant' and o.get('name')==tenant_name for o in nm.get('ownerReferences',[]))):
                raise Error('Namespace identity changed',409)
        cpu=s['resources']['cpu']['guaranteed'];memory=s['resources']['memory']['guaranteed']
        if (cpu%1000 or memory%1000 or any(v['guaranteed']!=v['maximum'] for v in s['resources'].values())):
            raise Error('Existing resource settings cannot be preserved by this API',409)
        body=dict(name=tenant_name,displayName=s['displayName'],contact=s.get('contact',''),pool=s['pool']['key'],
                  gpu=gpu,cpu=cpu//1000,memoryGB=memory//1000,resourceVersion=expected['resourceVersion'],
                  workloadsEnabled=s['mode']=='gpu-test',additionalNamespaceNames=s.get('additionalNamespaceNames',[]))
        code,saved=api.dispatch(principal,'PUT','/api/tenants/'+tenant_name,body)
        return dict(status='PolicyUpdated',httpStatus=code,policy=saved,queueVerified=False)
    except Error as exc:
        return dict(status='Deferred' if exc.status in (404,409,503) else 'Rejected',httpStatus=exc.status,reason=str(exc),queueVerified=False)
