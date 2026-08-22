"""In-memory Kubernetes boundary for behavioral tests; strict UID/RV semantics."""
import copy
import urllib.parse
from errors import Error
from workloads import template_spec, OWNER, MANAGED

BASE = '/apis/tenancy.gpu.platform.io/v1alpha1/tenantresourcepolicies'
TENANTS = '/apis/capsule.clastix.io/v1beta2/tenants'
QUEUES = '/apis/scheduling.run.ai/v2/queues'


class FakeKube:
    def __init__(self):
        self.objects = {}; self.calls = []; self.serial = 0
        self.fail_paths = set(); self.lose_create_response = False

    def call(self, path, method='GET', body=None, optional=False):
        self.calls.append((method, path))
        if path in self.fail_paths: raise Error('API unavailable', 503)
        parsed = urllib.parse.urlsplit(path); path = parsed.path
        if method == 'GET':
            if path in self.objects: return copy.deepcopy(self.objects[path])
            collections = ('/pods', '/resourceclaims', '/configmaps', '/namespaces',
                           '/tenants', '/tenantresourcepolicies', '/queues', '/resourceclaimtemplates',
                           '/resourceslices', '/nodes', '/events')
            if path.endswith(collections):
                rows = [copy.deepcopy(v) for key, v in self.objects.items()
                        if key.startswith(path + '/') and '/' not in key[len(path) + 1:]]
                return {'items': rows, 'metadata': {}}
            if optional: return None
            raise Error('Not found', 404)
        if method == 'DELETE':
            obj = self.objects.get(path)
            if not obj: raise Error('Not found', 404)
            if body.get('preconditions', {}).get('uid') != obj['metadata']['uid']:
                raise Error('UID conflict', 409)
            del self.objects[path]; return {'status': 'Success'}
        obj = copy.deepcopy(body)
        target = path + '/' + obj['metadata']['name'] if method == 'POST' else path.removesuffix('/status')
        old = self.objects.get(target)
        if method == 'POST' and old: raise Error('Already exists', 409)
        if method == 'PUT' and old and obj['metadata'].get('resourceVersion') != old['metadata']['resourceVersion']:
            raise Error('RV conflict', 409)
        self.serial += 1
        obj['metadata'].setdefault('uid', 'created-' + str(self.serial))
        obj['metadata']['resourceVersion'] = str(self.serial)
        obj['metadata'].setdefault('generation', 1)
        self.objects[target] = obj
        if method == 'POST' and self.lose_create_response and path.endswith('/pods'):
            self.lose_create_response = False
            raise Error('response lost', 503)
        return copy.deepcopy(obj)


def seeded():
    k = FakeKube()
    pool = {'key': 'mig-test', 'mode': 'mig', 'profile': '2g.48gb', 'model': 'PRO',
            'capacity': 2, 'nodes': ['node1']}
    p = {'metadata': {'name': 'team-a', 'uid': 'p-a', 'generation': 1, 'resourceVersion': '1',
                      'labels': {MANAGED: 'true'}},
         'spec': {'tenantName': 'team-a', 'namespaceName': 'tenant-team-a',
                  'pool': pool, 'displayName': 'A', 'contact': '', 'mode': 'gpu-test',
                  'resources': {x: {'guaranteed': n, 'maximum': n}
                                for x, n in [('gpu', 1), ('cpu', 4000), ('memory', 16000)]}},
         'status': {'tenantUID': 't-a', 'namespaceUID': 'ns-a', 'queue': 'tenant-a-q',
                    'queueUID': 'q-a', 'templateUID': 'tpl-a', 'phase': 'Ready',
                    'workloadsEnabled': True, 'observedGeneration': 1,
                    'namespaceBindings': {'tenant-team-a': {'uid': 'ns-a', 'templateUID': 'tpl-a', 'phase': 'Ready'}}}}
    k.objects[BASE + '/team-a'] = p
    k.objects[TENANTS + '/team-a'] = {'apiVersion': 'capsule.clastix.io/v1beta2', 'kind': 'Tenant',
        'metadata': {'name': 'team-a', 'uid': 't-a', 'resourceVersion': '1', 'labels': {OWNER: 'p-a'}},
        'spec': {'namespaceOptions': {'quota': 1}, 'owners': []}}
    k.objects['/api/v1/namespaces/tenant-team-a'] = {'metadata': {'name': 'tenant-team-a', 'uid': 'ns-a',
        'resourceVersion': '1', 'labels': {OWNER: 'p-a', MANAGED: 'true', 'capsule.clastix.io/tenant': 'team-a'},
        'ownerReferences': [{'kind': 'Tenant', 'name': 'team-a', 'uid': 't-a'}]}}
    k.objects[QUEUES + '/tenant-a-q'] = {'metadata': {'name': 'tenant-a-q', 'uid': 'q-a',
        'resourceVersion': '1', 'labels': {OWNER: 'p-a'}}, 'spec': {'resources': {
            x: {'quota': n, 'limit': n, 'overQuotaWeight': 1} for x, n in [('gpu', 1), ('cpu', 4000), ('memory', 16000)]}}}
    k.objects['/apis/resource.k8s.io/v1/namespaces/tenant-team-a/resourceclaimtemplates/tenant-gpu'] = {
        'metadata': {'name': 'tenant-gpu', 'uid': 'tpl-a', 'resourceVersion': '1', 'labels': {OWNER: 'p-a'}},
        'spec': template_spec(pool)}
    k.objects['/apis/resource.k8s.io/v1/resourceslices/s1'] = {'metadata': {'name': 's1'},
        'spec': {'driver': 'gpu.nvidia.com', 'nodeName': 'node1',
                 'pool': {'name': 'node1', 'generation': 1, 'resourceSliceCount': 1},
                 'devices': [{'name': 'mig1', 'attributes': {
                     'type': {'string': 'mig'}, 'productName': {'string': 'PRO'}, 'profile': {'string': '2g.48gb'}}}]}}
    return k, copy.deepcopy(p)
