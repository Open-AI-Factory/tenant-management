"""Observe held Kueue budget bindings; never submit or write a workload."""
import copy
from decimal import Decimal, InvalidOperation
from errors import Error
from kubeutil import read, listing, TENANTS, QUEUES
from tenant_budget_policy import validate_policy

CQ = '/apis/kueue.x-k8s.io/v1beta2/clusterqueues'
RF = '/apis/kueue.x-k8s.io/v1beta2/resourceflavors'
KUEUE = '/apis/kueue.x-k8s.io/v1beta2'


def devices(value):
    if isinstance(value, bool):
        raise ValueError('Invalid device quantity')
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n != n.to_integral_value():
            raise ValueError('Non-integral device quota')
        return int(n)
    except (InvalidOperation, TypeError):
        raise ValueError('Invalid device quantity')


def gpu_resource(cq, policy):
    matches = []
    for group in cq.get('spec', {}).get('resourceGroups', []):
        for flavor in group.get('flavors', []):
            if flavor.get('name') == policy['flavor']['name']:
                matches.extend(r for r in flavor.get('resources', []) if r.get('name') == policy['resource'])
    if len(matches) != 1:
        raise ValueError('Exactly one GPU quota path required')
    return matches[0]


def pinned(kube, path, ref):
    obj = read(kube, path)
    m = obj.get('metadata', {})
    if m.get('uid') != ref['uid'] or m.get('name') != ref['name'] or m.get('deletionTimestamp'):
        raise Error('Object identity changed', 409, 'IdentityMismatch')
    return obj


def status_devices(status, field, policy):
    rows = status.get(field)
    if not isinstance(rows, list):
        return None
    values = [r.get('total') for f in rows if f.get('name') == policy['flavor']['name']
              for r in f.get('resources', []) if r.get('name') == policy['resource']]
    try:
        return devices(values[0]) if len(values) == 1 else None
    except ValueError:
        return None


def execution_binding(policy: dict, coverage: dict) -> dict:
    return {'tenantUID': policy['tenantUID'], 'policyRevision': policy['revision'],
            'namespaces': copy.deepcopy(policy['namespaces']), 'clusterQueue': copy.deepcopy(policy['clusterQueue']),
            'placement': copy.deepcopy(policy['placement']),
            'labels': {'kueue.x-k8s.io/queue-name': policy['namespaces'][0]['localQueue']['name'],
                       'kai.scheduler/queue': policy['placement']['kaiQueueName'],
                       'gpu.platform.io/kueue-experiment': 'true'},
            'admissionCoverage': copy.deepcopy(coverage), 'executionEnabled': False,
            'reason': 'HeldTestQueue'}


def observe_binding(kube, policy: dict, targets: dict) -> dict:
    result = {'tenantUID': policy.get('tenantUID'), 'policyRevision': policy.get('revision'),
              'policyDigest': None, 'clusterQueue': None, 'quota': None, 'resourceVersion': None,
              'generation': None, 'capsuleQuota': {'state': 'Unknown'}, 'usage': None,
              'reservation': None, 'controller': {}, 'effective': {'state': 'Unknown', 'reason': 'NominalOnly'},
              'guards': {'ok': False, 'reason': 'Unavailable'}}
    try:
        p = validate_policy(policy, targets)
        result['policyDigest'] = p['digest']
        tenant = pinned(kube, TENANTS + '/' + p['tenantName'], {'name': p['tenantName'], 'uid': p['tenantUID']})
        quotas = tenant.get('spec', {}).get('resourceQuotas')
        result['capsuleQuota'] = {'state': 'Present' if quotas is not None else 'NotConfigured', 'raw': quotas}
        for ns in p['namespaces']:
            obj = pinned(kube, '/api/v1/namespaces/' + ns['name'], ns)
            meta = obj['metadata']
            if (meta.get('labels', {}).get('capsule.clastix.io/tenant') != p['tenantName'] or
                    not any(o.get('kind') == 'Tenant' and o.get('name') == p['tenantName'] and
                            o.get('uid') == p['tenantUID'] for o in meta.get('ownerReferences', []))):
                raise Error('Capsule membership mismatch', 409, 'IdentityMismatch')
            base = '/api/v1/namespaces/' + ns['name']
            lock = pinned(kube, base + '/resourcequotas/' + ns['resourceQuota']['name'], ns['resourceQuota'])
            hard = lock.get('spec', {}).get('hard', {})
            if hard.get('pods') not in ('0', 0) or hard.get('count/resourceclaims.resource.k8s.io') not in ('0', 0):
                raise Error('Namespace execution lock changed', 409, 'ExecutionLockChanged')
            lbase = KUEUE + '/namespaces/' + ns['name']
            lq = pinned(kube, lbase + '/localqueues/' + ns['localQueue']['name'], ns['localQueue'])
            if lq.get('spec', {}).get('clusterQueue') != p['clusterQueue']['name']:
                raise Error('LocalQueue membership changed', 409, 'IdentityMismatch')
            if (listing(kube, lbase + '/workloads') or listing(kube, base + '/pods') or
                    listing(kube, '/apis/resource.k8s.io/v1/namespaces/' + ns['name'] + '/resourceclaims')):
                raise Error('Test namespace has workloads', 409, 'WorkloadsPresent')
        flavor = pinned(kube, RF + '/' + p['flavor']['name'], p['flavor'])
        if flavor.get('spec', {}).get('nodeLabels') != {'nvidia.com/gpu.product': 'NVIDIA-RTX-A6000'}:
            raise Error('Flavor selector changed', 409, 'FlavorChanged')
        pinned(kube, QUEUES + '/' + p['placement']['kaiQueueName'],
               {'name': p['placement']['kaiQueueName'], 'uid': p['placement']['kaiQueueUID']})
        cq = pinned(kube, CQ + '/' + p['clusterQueue']['name'], p['clusterQueue'])
        result.update(clusterQueue=cq, resourceVersion=cq['metadata'].get('resourceVersion'),
                      generation=cq['metadata'].get('generation'))
        spec = cq.get('spec', {})
        if (spec.get('stopPolicy') != 'Hold' or spec.get('queueingStrategy') != 'StrictFIFO' or
                spec.get('cohort') or spec.get('cohortName')):
            raise Error('Held queue contract changed', 409, 'QueueGuardChanged')
        selector = spec.get('namespaceSelector', {})
        names = [ns['name'] for ns in p['namespaces']]
        valid_selectors = [{'matchLabels': {'kubernetes.io/metadata.name': names[0]}}] if len(names) == 1 else []
        valid_selectors.append({'matchExpressions': [{'key': 'kubernetes.io/metadata.name', 'operator': 'In', 'values': names}]})
        if selector not in valid_selectors:
            raise Error('Queue namespace selector is not exclusive', 409, 'QueueGuardChanged')
        preemption = spec.get('preemption', {})
        if any(preemption.get(key, 'Never') != 'Never' for key in ('withinClusterQueue', 'reclaimWithinCohort')):
            raise Error('Preemption enabled', 409, 'QueueGuardChanged')
        resource = gpu_resource(cq, p)
        if any(devices(resource.get(key, 0)) != 0 for key in ('borrowingLimit', 'lendingLimit')):
            raise Error('Borrowing/lending enabled', 409, 'QueueGuardChanged')
        result['quota'] = devices(resource['nominalQuota'])
        status = cq.get('status', {})
        result['usage'] = status_devices(status, 'flavorsUsage', p)
        result['reservation'] = status_devices(status, 'flavorsReservation', p)
        result['controller'] = {'conditions': copy.deepcopy(status.get('conditions', [])),
                                'generation': result['generation']}
        if quotas is not None and 'nvidia.com/gpu' in str(quotas):
            raise Error('Capsule GPU quota semantics unresolved', 409, 'CapsuleQuotaUnresolved')
        if not result['resourceVersion'] or not result['generation']:
            raise Error('Queue version unavailable', 503, 'Unavailable')
        result['guards'] = {'ok': True, 'reason': 'HeldBindingVerified'}
    except (Error, ValueError, KeyError, TypeError, AttributeError) as exc:
        result['guards'] = {'ok': False, 'reason': getattr(exc, 'code', None) or
                           ('Unavailable' if getattr(exc, 'status', 0) >= 500 else 'InvalidBinding'), 'detail': str(exc)}
    return result
