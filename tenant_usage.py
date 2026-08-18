"""Read-only DRA usage across the current tenant's managed namespaces.

Allocation is evidence from ResourceClaims, not Pod phase or the execution
journal. Counts are an observation, not a reservation or a release guarantee.
"""
from errors import Error
from executions import now
from kubeutil import read, listing, BASE, TENANTS, OWNER, MANAGED
from namespaces import policy_namespaces


def unknown(reason):
    return dict(status='Unknown', allocatedDevices=None, waitingJobs=None,
                observedAt=None, reason=reason, scope='tenant-dra')


def check_identity(kube, tenant, policy):
    """Bracket collection with live UID/ownership checks; reject replacements."""
    current = read(kube, TENANTS + '/' + tenant['name'])['metadata']
    if current['uid'] != tenant['uid'] or current.get('deletionTimestamp'):
        raise Error('Tenant identity changed', 409, 'IdentityMismatch')
    live = read(kube, BASE + '/' + policy['metadata']['name'])
    if (live['metadata']['uid'] != policy['metadata']['uid'] or
            live['metadata'].get('deletionTimestamp') or
            live['metadata'].get('resourceVersion') != policy['metadata'].get('resourceVersion')):
        raise Error('Policy changed during observation', 409, 'IdentityMismatch')
    bindings = policy.get('status', {}).get('namespaceBindings', {})
    for space in tenant['namespaces']:
        meta = read(kube, '/api/v1/namespaces/' + space['name'])['metadata']
        if (meta['uid'] != space['uid'] or meta.get('deletionTimestamp') or
                bindings.get(space['name'], {}).get('uid') != space['uid'] or
                meta.get('labels', {}).get(OWNER) != policy['metadata']['uid'] or
                meta.get('labels', {}).get('capsule.clastix.io/tenant') != tenant['name'] or
                not any(owner.get('kind') == 'Tenant' and owner.get('name') == tenant['name'] and
                        owner.get('uid') == tenant['uid'] for owner in meta.get('ownerReferences', []))):
            raise Error('Namespace identity changed', 409, 'IdentityMismatch')


def allocated_devices(claims, namespace):
    devices = set()
    for claim in claims:
        if claim['metadata'].get('namespace') != namespace:
            raise ValueError('ResourceClaim outside requested namespace')
        allocation = claim.get('status', {}).get('allocation')
        if allocation is None:
            continue
        results = allocation['devices']['results']
        if not isinstance(results, list):
            raise ValueError('Incomplete device allocation')
        for result in results:
            driver = result['driver']
            if not isinstance(driver, str) or not driver:
                raise ValueError('Missing allocation driver')
            if driver != 'gpu.nvidia.com' or result.get('adminAccess') is True:
                continue
            key = (driver, result['pool'], result['device'])
            if not all(isinstance(value, str) and value for value in key):
                raise ValueError('Incomplete device identity')
            devices.add(key)
    return devices


def waiting_jobs(pods, namespace, policy_uid):
    waiting = set()
    for pod in pods:
        meta, spec = pod['metadata'], pod.get('spec', {})
        if meta.get('namespace') != namespace:
            raise ValueError('Pod outside requested namespace')
        labels = meta.get('labels', {})
        if (labels.get(OWNER) != policy_uid or labels.get(MANAGED) != 'true' or
                labels.get('tenancy.gpu.platform.io/job') != 'gpu-smoke' or
                not any(claim.get('resourceClaimTemplateName') == 'tenant-gpu'
                        for claim in spec.get('resourceClaims', []))):
            continue
        if meta.get('deletionTimestamp'):
            continue
        status = pod.get('status', {})
        phase = status.get('phase', 'Pending')
        if phase not in ('Pending', 'Running', 'Succeeded', 'Failed'):
            raise ValueError('Unknown GPU Pod phase')
        scheduled = any(condition.get('type') == 'PodScheduled' and condition.get('status') == 'True'
                        for condition in status.get('conditions', []))
        if phase == 'Pending' and not spec.get('nodeName') and not scheduled:
            waiting.add(meta['uid'])
    return len(waiting)


def observe_usage(kube, tenant):
    policy = tenant.get('policy')
    if tenant['policyState'] in ('IdentityMismatch', 'UnsupportedPath', 'Unavailable'):
        return unknown(tenant['policyState'])
    if policy is None:
        return unknown('PolicyMissing')
    try:
        if (policy['metadata'].get('labels', {}).get(MANAGED) != 'true' or
                policy.get('status', {}).get('tenantUID') != tenant['uid'] or
                set(policy_namespaces(policy)) != {space['name'] for space in tenant['namespaces']}):
            return unknown('IdentityMismatch')
        check_identity(kube, tenant, policy)
        devices, waiting = set(), 0
        for space in tenant['namespaces']:
            namespace = space['name']
            claims = listing(kube, '/apis/resource.k8s.io/v1/namespaces/' + namespace + '/resourceclaims')
            pods = listing(kube, '/api/v1/namespaces/' + namespace + '/pods')
            devices.update(allocated_devices(claims, namespace))
            waiting += waiting_jobs(pods, namespace, policy['metadata']['uid'])
        check_identity(kube, tenant, policy)
        return dict(status='Observed', allocatedDevices=len(devices), waitingJobs=waiting,
                    observedAt=now(), reason=None, scope='tenant-dra')
    except Error as exc:
        return unknown('Unavailable' if exc.status == 503 else 'IdentityMismatch')
    except (KeyError, TypeError, ValueError, AttributeError):
        return unknown('IncompleteObservation')
