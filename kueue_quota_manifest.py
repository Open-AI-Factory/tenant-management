"""Operator-only, narrowly scoped held-budget rollout preparation."""
import copy
import hashlib
import json

RUN_LABEL = 'tenancy.gpu.platform.io/budget-run-id'
SA = [{'kind': 'ServiceAccount', 'name': 'tenant-admin', 'namespace': 'monitoring'}]
RBAC = 'rbac.authorization.k8s.io/v1'


def prepare(before, code):
    dep = before['deployment']
    spec = copy.deepcopy(dep['spec'])
    pod = spec['template']['spec']
    if spec.get('replicas') != 1 or spec.get('strategy', {}).get('type') != 'Recreate' or pod.get('serviceAccountName') != 'tenant-admin':
        raise ValueError('Unexpected deployment contract')
    containers = [c for c in pod['containers'] if c['name'] == 'console']
    if len(containers) != 1: raise ValueError('Unexpected console')
    c = containers[0]
    env = {e['name']: e.get('value') for e in c.get('env', [])}
    if any(env.get(k) != 'false' for k in ('TENANT_MUTATIONS_ENABLED', 'TENANT_RECONCILE_ENABLED')):
        raise ValueError('Existing mutation gates must remain closed')
    code_digest = hashlib.sha256(json.dumps(code, sort_keys=True).encode()).hexdigest()
    name = 'tenant-admin-budget-' + code_digest[:12]
    volumes = [v for v in pod['volumes'] if v['name'] == 'code']
    if len(volumes) != 1: raise ValueError('Code volume missing')
    volumes[0]['configMap']['name'] = name
    spec['template'].setdefault('metadata', {}).setdefault('annotations', {})['checksum/code'] = code_digest
    for e in c['env']:
        if e['name'] == 'KUEUE_QUOTA_ENABLED': e.clear(); e.update(name='KUEUE_QUOTA_ENABLED', value='true'); break
    else: c['env'].append({'name': 'KUEUE_QUOTA_ENABLED', 'value': 'true'})
    return {'before': copy.deepcopy(before), 'expectedSpec': spec, 'digest': code_digest,
            'configMap': {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': 'monitoring'},
                          'immutable': True, 'data': code}}


def budget_rbac():
    rules = [
        {'apiGroups':['kueue.x-k8s.io'], 'resources':['clusterqueues'], 'resourceNames':['example-tenant-queue-a','example-tenant-queue-b'], 'verbs':['get','update']},
        {'apiGroups':['kueue.x-k8s.io'], 'resources':['resourceflavors'], 'resourceNames':['example-gpu-flavor'], 'verbs':['get']},
        {'apiGroups':['capsule.clastix.io'], 'resources':['tenants'], 'resourceNames':['example-tenant-a','example-tenant-b'], 'verbs':['get']},
        {'apiGroups':['scheduling.run.ai'], 'resources':['queues'], 'resourceNames':['example-kai-queue'], 'verbs':['get']}]
    objects = [{'apiVersion':RBAC,'kind':'ClusterRole','metadata':{'name':'tenant-admin-budget'},'rules':rules},
               {'apiVersion':RBAC,'kind':'ClusterRoleBinding','metadata':{'name':'tenant-admin-budget'},
                'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':'tenant-admin-budget'},'subjects':SA}]
    for letter in ('a','b'):
        ns = 'example-tenant-queue-' + letter
        rules = [
            {'apiGroups':['kueue.x-k8s.io'], 'resources':['localqueues'], 'resourceNames':['example-tenant-'+letter], 'verbs':['get']},
            {'apiGroups':['kueue.x-k8s.io'], 'resources':['workloads'], 'verbs':['list']},
            {'apiGroups':[''], 'resources':['pods'], 'verbs':['list']},
            {'apiGroups':['resource.k8s.io'], 'resources':['resourceclaims'], 'verbs':['list']},
            {'apiGroups':[''], 'resources':['resourcequotas'], 'resourceNames':['execution-lock'], 'verbs':['get']}]
        objects.extend([{'apiVersion':RBAC,'kind':'Role','metadata':{'name':'tenant-admin-budget','namespace':ns},'rules':rules},
                        {'apiVersion':RBAC,'kind':'RoleBinding','metadata':{'name':'tenant-admin-budget','namespace':ns},
                         'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'tenant-admin-budget'},'subjects':SA}])
    objects.extend([{'apiVersion':RBAC,'kind':'Role','metadata':{'name':'tenant-admin-budget','namespace':'monitoring'},'rules':[
        {'apiGroups':[''],'resources':['configmaps'],'resourceNames':['tenant-admin-budget-targets','tenant-admin-budget-policy','tenant-admin-budget-state'],'verbs':['get']},
        {'apiGroups':[''],'resources':['configmaps'],'resourceNames':['tenant-admin-budget-state'],'verbs':['update']}]},
        {'apiVersion':RBAC,'kind':'RoleBinding','metadata':{'name':'tenant-admin-budget','namespace':'monitoring'},
         'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'tenant-admin-budget'},'subjects':SA}])
    return objects


def readonly_guard():
    name = 'tenant-budget-policy-readonly'
    return [{'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicy','metadata':{'name':name},'spec':{
        'failurePolicy':'Fail',
        'matchConstraints':{'resourceRules':[{'apiGroups':[''],'apiVersions':['v1'],'operations':['CREATE','UPDATE','DELETE'],'resources':['configmaps'],'scope':'Namespaced'}]},
        'matchConditions':[{'name':'operator-records','expression':
            "request.namespace == 'monitoring' && request.name in ['tenant-admin-budget-targets', 'tenant-admin-budget-policy']"}],
        'validations':[{'expression':"request.userInfo.username != 'system:serviceaccount:monitoring:tenant-admin'",
                        'message':'Tenant budget targets/policy require operator authority'}]}},
        {'apiVersion':'admissionregistration.k8s.io/v1','kind':'ValidatingAdmissionPolicyBinding','metadata':{'name':name},
         'spec':{'policyName':name,'validationActions':['Deny']}}]


def bootstrap_objects(run_id):
    owners=[{'name':'system:serviceaccount:monitoring:tenant-admin','kind':'User','clusterRoles':[]}]
    return [{'apiVersion':'capsule.clastix.io/v1beta2','kind':'Tenant',
        'metadata':{'name':'example-tenant-'+s,'labels':{RUN_LABEL:run_id}},
        'spec':{'owners':copy.deepcopy(owners), 'namespaceOptions':{'quota':1}}} for s in ('a','b')]
