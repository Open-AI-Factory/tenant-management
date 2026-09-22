"""Strict Kubernetes boundary fixtures for the held native GPU budget path."""
import copy
import json
from errors import Error
from test_support import FakeKube

CQ = '/apis/kueue.x-k8s.io/v1beta2/clusterqueues'
RF = '/apis/kueue.x-k8s.io/v1beta2/resourceflavors'
TENANTS = '/apis/capsule.clastix.io/v1beta2/tenants'
KAI = '/apis/scheduling.run.ai/v2/queues'
CM = '/api/v1/namespaces/monitoring/configmaps'


class KueueKube(FakeKube):
    def __init__(self):
        super().__init__()
        self.after_write = None
        self.before_call = None

    def call(self, path, method='GET', body=None, optional=False):
        if self.before_call:
            self.before_call(path, method, body)
        if method == 'GET' and path not in self.objects and path.endswith(
                ('/clusterqueues', '/localqueues', '/resourceflavors', '/workloads', '/resourcequotas')):
            self.calls.append((method, path))
            if path in self.fail_paths:
                raise Error('Unavailable', 503)
            return {'items': [copy.deepcopy(v) for key, v in self.objects.items()
                              if key.startswith(path + '/') and '/' not in key[len(path) + 1:]],
                    'metadata': {}}
        old = copy.deepcopy(self.objects.get(path))
        if method == 'PUT' and old and body['metadata'].get('uid') != old['metadata'].get('uid'):
            raise Error('UID conflict', 409)
        result = super().call(path, method, body, optional)
        if method == 'PUT' and old and old.get('spec') != result.get('spec'):
            self.objects[path]['metadata']['generation'] = old['metadata'].get('generation', 1) + 1
            result = copy.deepcopy(self.objects[path])
        if method != 'GET' and self.after_write:
            self.after_write(path, method, result)
        return result


def seed_kueue():
    k = KueueKube()
    k.serial = 1  # seeded objects already have RV=1; the first PUT must advance it
    targets = {'schemaVersion': 1, 'envelopeDevices': 2, 'poolKey': 'example-gpu-pool',
               'resource': 'nvidia.com/gpu', 'unit': 'device', 'entries': {},
               'admissionCoverage': {'state': 'NotCovered', 'reason': 'example namespace only'}}
    policies = {'schemaVersion': 1, 'entries': {}}
    state = {'schemaVersion': 1, 'tenants': {}}
    for letter, quota in [('a', 1), ('b', 0)]:
        name, ns, uid = 'example-tenant-' + letter, 'example-tenant-queue-' + letter, 't-' + letter
        p = {'tenantName': name, 'tenantUID': uid, 'revision': 1, 'backend': 'kueue',
             'source': 'dummy-profiler', 'namespaces': [{'name': ns, 'uid': 'ns-' + letter,
                  'localQueue': {'name': name, 'uid': 'lq-' + letter},
                  'resourceQuota': {'name': 'execution-lock', 'uid': 'rq-' + letter}}],
             'clusterQueue': {'name': ns, 'uid': 'cq-' + letter},
             'flavor': {'name': 'example-gpu-flavor', 'uid': 'flavor-1'},
             'poolKey': 'example-gpu-pool', 'resource': 'nvidia.com/gpu', 'unit': 'device',
             'minDevices': 0, 'maxDevices': 2, 'leadSeconds': 30, 'downHoldSeconds': 60,
             'cooldownSeconds': 30, 'maxAgeSeconds': 120, 'minConfidence': 0.8,
             'maxSampleGapSeconds': 30, 'headroomDevices': 0,
             'placement': {'schedulerName': 'kai-scheduler', 'kaiQueueName': 'example-kai-queue',
                           'kaiQueueUID': 'kai-shared'}}
        targets['entries'][uid] = {key: copy.deepcopy(p[key]) for key in
            ('tenantName', 'tenantUID', 'namespaces', 'clusterQueue', 'flavor', 'placement')}
        policies['entries'][uid] = p
        k.objects[TENANTS + '/' + name] = {'apiVersion': 'capsule.clastix.io/v1beta2', 'kind': 'Tenant',
            'metadata': {'name': name, 'uid': uid, 'resourceVersion': '1'},
            'spec': {'namespaceOptions': {'quota': 1}, 'owners': []}}
        k.objects['/api/v1/namespaces/' + ns] = {'kind': 'Namespace',
            'metadata': {'name': ns, 'uid': 'ns-' + letter, 'resourceVersion': '1',
                'labels': {'capsule.clastix.io/tenant': name},
                'ownerReferences': [{'kind': 'Tenant', 'name': name, 'uid': uid}]}}
        k.objects['/api/v1/namespaces/' + ns + '/resourcequotas/execution-lock'] = {
            'metadata': {'name': 'execution-lock', 'uid': 'rq-' + letter, 'resourceVersion': '1'},
            'spec': {'hard': {'pods': '0', 'count/resourceclaims.resource.k8s.io': '0'}}}
        k.objects['/apis/kueue.x-k8s.io/v1beta2/namespaces/' + ns + '/localqueues/' + name] = {
            'metadata': {'name': name, 'namespace': ns, 'uid': 'lq-' + letter, 'resourceVersion': '1'},
            'spec': {'clusterQueue': ns}}
        k.objects[CQ + '/' + ns] = {'apiVersion': 'kueue.x-k8s.io/v1beta2', 'kind': 'ClusterQueue',
            'metadata': {'name': ns, 'uid': 'cq-' + letter, 'resourceVersion': '1', 'generation': 1},
            'spec': {'stopPolicy': 'Hold', 'queueingStrategy': 'StrictFIFO',
                'namespaceSelector': {'matchLabels': {'kubernetes.io/metadata.name': ns}},
                'preemption': {'withinClusterQueue': 'Never', 'reclaimWithinCohort': 'Never'},
                'resourceGroups': [{'coveredResources': ['nvidia.com/gpu', 'cpu', 'memory'],
                  'flavors': [{'name': 'example-gpu-flavor', 'resources': [
                    {'name': 'nvidia.com/gpu', 'nominalQuota': str(quota)},
                    {'name': 'cpu', 'nominalQuota': '16'}, {'name': 'memory', 'nominalQuota': '48Gi'}]}]}]}}
    k.objects[RF + '/example-gpu-flavor'] = {
        'metadata': {'name': 'example-gpu-flavor', 'uid': 'flavor-1', 'resourceVersion': '1'},
        'spec': {'nodeLabels': {'nvidia.com/gpu.product': 'NVIDIA-RTX-A6000'}}}
    k.objects[KAI + '/example-kai-queue'] = {
        'metadata': {'name': 'example-kai-queue', 'uid': 'kai-shared', 'resourceVersion': '1'},
        'spec': {'resources': {'gpu': {'quota': 2, 'limit': 2}}}}
    for name, doc in [('targets', targets), ('policy', policies), ('state', state)]:
        k.objects[CM + '/tenant-admin-budget-' + name] = {'apiVersion': 'v1', 'kind': 'ConfigMap',
            'metadata': {'name': 'tenant-admin-budget-' + name, 'namespace': 'monitoring',
                         'uid': 'cm-' + name, 'resourceVersion': '1'},
            'data': {'document': json.dumps(doc)}}
    return k, targets, policies, state
