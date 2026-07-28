"""Fresh identity and resource-path checks; no admission from cached UI state."""
from errors import Error
from kubeutil import read, listing, BASE, TENANTS, QUEUES, OWNER
from namespaces import policy_namespaces
from workloads import template_spec


def require(value, message, code='IdentityMismatch'):
    if not value: raise Error(message, 409, code)


def supported_nodes(kube, pool):
    slices = listing(kube, '/apis/resource.k8s.io/v1/resourceslices')
    latest = {}
    for obj in slices:
        s = obj['spec']; key = (s['driver'], s['pool']['name'])
        latest[key] = max(latest.get(key, -1), s['pool']['generation'])
    groups = {}
    for obj in slices:
        s = obj['spec']; key = (s['driver'], s['pool']['name'])
        if s['driver'] == 'gpu.nvidia.com' and s['pool']['generation'] == latest[key]:
            groups.setdefault(key, []).append(s)
    nodes, seen = set(), set()
    for group in groups.values():
        if any(s['pool'].get('resourceSliceCount') != len(group) for s in group):
            raise Error('DRA 자원풀 세대가 완전하지 않습니다.', 503, 'Unavailable')
        for s in group:
            for d in s.get('devices', []):
                key = (s['driver'], s['pool']['name'], d['name'])
                if key in seen: raise Error('DRA 장치가 중복됐습니다.', 503, 'Unavailable')
                seen.add(key); attrs = d.get('attributes', {})
                if (attrs.get('productName', {}).get('string') == pool['model'] and
                    attrs.get('type', {}).get('string') == ('mig' if pool['mode'] == 'mig' else 'gpu') and
                    (pool['mode'] != 'mig' or attrs.get('profile', {}).get('string') == pool.get('profile'))):
                    node = s.get('nodeName')
                    if node in pool['nodes']: nodes.add(node)
    require(nodes, '현재 정책에 맞는 DRA 경로가 없습니다. native 경로는 별도 연동이 필요합니다.', 'UnsupportedPath')
    return tuple(sorted(nodes))


def resolve_context(kube, policy, namespace_name):
    p = read(kube, BASE + '/' + policy['metadata']['name'])
    pm, spec, status = p['metadata'], p['spec'], p.get('status', {})
    require(pm['uid'] == policy['metadata']['uid'] and pm['generation'] == policy['metadata']['generation'],
            '정책 버전이 변경되었습니다.', 'Reconciling')
    require(not pm.get('deletionTimestamp') and spec['mode'] == 'gpu-test' and
            status.get('phase') == 'Ready' and status.get('workloadsEnabled') is True and
            status.get('observedGeneration') == pm['generation'], '정책 적용이 완료되지 않았습니다.', 'Reconciling')
    require(namespace_name in policy_namespaces(p), '허용되지 않은 Namespace입니다.')
    t = read(kube, TENANTS + '/' + spec['tenantName']); tm = t['metadata']
    require(tm['uid'] == status.get('tenantUID') and not tm.get('deletionTimestamp'), 'Tenant UID가 변경되었습니다.')
    binding = status.get('namespaceBindings', {}).get(namespace_name)
    require(binding and binding.get('phase') == 'Ready', 'Namespace 적용이 완료되지 않았습니다.', 'Reconciling')
    n = read(kube, '/api/v1/namespaces/' + namespace_name); nm = n['metadata']
    require(nm['uid'] == binding['uid'] and not nm.get('deletionTimestamp') and
            nm.get('labels', {}).get(OWNER) == pm['uid'] and
            nm.get('labels', {}).get('capsule.clastix.io/tenant') == tm['name'] and
            any(o.get('kind') == 'Tenant' and o.get('name') == tm['name'] and o.get('uid') == tm['uid']
                for o in nm.get('ownerReferences', [])), 'Namespace 소유권이 일치하지 않습니다.')
    q = read(kube, QUEUES + '/' + status['queue']); qm = q['metadata']
    expected = {key: {'quota': v['guaranteed'], 'limit': v['maximum'], 'overQuotaWeight': 1}
                for key, v in spec['resources'].items()}
    require(qm['uid'] == status.get('queueUID') and not qm.get('deletionTimestamp') and
            qm.get('labels', {}).get(OWNER) == pm['uid'] and q['spec'].get('resources') == expected,
            'KAI 큐의 UID 또는 자원 정책이 변경되었습니다.')
    template = read(kube, '/apis/resource.k8s.io/v1/namespaces/' + namespace_name + '/resourceclaimtemplates/tenant-gpu')
    require(template['metadata']['uid'] == binding['templateUID'] and
            not template['metadata'].get('deletionTimestamp') and
            template['metadata'].get('labels', {}).get(OWNER) == pm['uid'] and
            template['spec'] == template_spec(spec['pool']), 'GPU 요청 템플릿이 변경되었습니다.')
    nodes = supported_nodes(kube, spec['pool'])
    return dict(tenantName=tm['name'], tenantUID=tm['uid'], namespaceName=namespace_name,
                namespaceUID=nm['uid'], policyName=pm['name'], policyUID=pm['uid'], policyGeneration=pm['generation'],
                queueName=qm['name'], queueUID=qm['uid'], templateName='tenant-gpu', templateUID=template['metadata']['uid'],
                poolKey=spec['pool']['key'], resourceKind=spec['pool']['mode'] + ':' + spec['pool']['model'] + ':' + spec['pool'].get('profile', ''),
                allocationPath='dra', allowedNodes=list(nodes))
