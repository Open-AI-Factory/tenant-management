import re
from errors import Error
from kubeutil import listing, ensure, metadata, OWNER, TENANTS
from workloads import template_spec


def policy_namespaces(policy):
    spec = policy['spec']; main = spec['namespaceName']
    extra = spec.get('additionalNamespaceNames', [])
    if (not isinstance(extra, list) or len(extra) > 2 or
        any(not isinstance(n, str) or not re.fullmatch(re.escape(main) + r'-[a-z0-9]{1,10}', n) for n in extra) or
        len(set(extra)) != len(extra)):
        raise Error('추가 Namespace는 기본 이름에 용도명을 붙이고 최대 2개까지 지정하세요.')
    return (main,) + tuple(extra)


def assert_policy_editable(kube, policy, pending_runs):
    if any(r.get('phase') in ('Prepared', 'Submitting', 'Submitted', 'Running', 'Pending') or
           r.get('release') in ('Pending', 'Unknown') for r in pending_runs):
        raise Error('미완료 실행 또는 반납 확인이 남아 있습니다.', 409)
    for ns in policy_namespaces(policy):
        if listing(kube, '/api/v1/namespaces/' + ns + '/pods') or listing(
                kube, '/apis/resource.k8s.io/v1/namespaces/' + ns + '/resourceclaims'):
            raise Error('작업과 ResourceClaim을 정리한 뒤 설정을 변경하세요.', 409)


def reconcile_namespaces(kube, policy, tenant):
    spaces = policy_namespaces(policy); result = {}
    pm = policy['metadata']; tm = tenant['metadata']; spec = policy['spec']
    if tm.get('labels', {}).get(OWNER) != pm['uid'] or tm.get('deletionTimestamp'):
        raise Error('기존 Tenant를 인수할 수 없습니다.', 409)
    old_tenant_uid = policy.get('status', {}).get('tenantUID')
    if old_tenant_uid and old_tenant_uid != tm['uid']:
        raise Error('Tenant UID가 변경되었습니다.', 409)
    known = policy.get('status', {}).get('namespaceBindings', {})
    if not known and policy.get('status', {}).get('namespaceUID'):
        known = {spec['namespaceName']: {'uid': policy['status']['namespaceUID'],
                                       'templateUID': policy['status'].get('templateUID')}}
    # Only our Tenant's namespace quota is changed; owners and all other fields survive.
    if len(spaces) > tenant.get('spec', {}).get('namespaceOptions', {}).get('quota', 1):
        tenant.setdefault('spec', {}).setdefault('namespaceOptions', {})['quota'] = 3
        kube.call(TENANTS + '/' + tm['name'], 'PUT', tenant)
    for ns in spaces:
        nm = metadata(ns, pm['uid']); nm['labels'].update({
            'capsule.clastix.io/tenant': tm['name'], 'pod-security.kubernetes.io/enforce': 'restricted'})
        nm['ownerReferences'] = [{'apiVersion': 'capsule.clastix.io/v1beta2', 'kind': 'Tenant',
            'name': tm['name'], 'uid': tm['uid'], 'controller': True, 'blockOwnerDeletion': True}]
        namespace = ensure(kube, '/api/v1/namespaces', {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': nm}, pm['uid'])
        m = namespace['metadata']
        if (m.get('labels', {}).get('capsule.clastix.io/tenant') != tm['name'] or
            not any(o.get('kind') == 'Tenant' and o.get('name') == tm['name'] and o.get('uid') == tm['uid'] for o in m.get('ownerReferences', [])) or
            (ns in known and known[ns]['uid'] != m['uid'])):
            raise Error('Namespace 소유권 또는 UID가 변경되었습니다.', 409)
        path = '/apis/resource.k8s.io/v1/namespaces/' + ns + '/resourceclaimtemplates'
        template = ensure(kube, path, {'apiVersion': 'resource.k8s.io/v1', 'kind': 'ResourceClaimTemplate',
            'metadata': metadata('tenant-gpu', pm['uid'], ns), 'spec': template_spec(spec['pool'])}, pm['uid'])
        if template['spec'] != template_spec(spec['pool']) or (
            ns in known and known[ns].get('templateUID') and known[ns]['templateUID'] != template['metadata']['uid']):
            raise Error('ClaimTemplate 명세 또는 UID가 변경되었습니다.', 409)
        result[ns] = {'uid': m['uid'], 'templateUID': template['metadata']['uid'], 'phase': 'Ready'}
    return result
