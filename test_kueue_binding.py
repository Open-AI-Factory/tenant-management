import unittest
from test_kueue_support import seed_kueue, CQ, TENANTS, RF
try:
    from kueue_binding import observe_binding, execution_binding
except ImportError:
    observe_binding = execution_binding = None


class Tests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(observe_binding, 'Kueue binding is not implemented')
        self.k, self.targets, self.policies, _ = seed_kueue()
        self.p = self.policies['entries']['t-a']

    def observe(self):
        return observe_binding(self.k, self.p, self.targets)

    def test_v1beta2_cohort_name_is_rejected(self):
        self.k.objects[CQ+'/example-tenant-queue-a']['spec']['cohortName'] = 'shared'
        self.assertFalse(self.observe()['guards']['ok'])

    def test_malformed_status_is_unknown_instead_of_exception(self):
        self.k.objects[CQ+'/example-tenant-queue-a']['status'] = {'flavorsUsage': [None]}
        self.assertFalse(self.observe()['guards']['ok'])

    def test_bindings_are_disjoint_and_observation_does_not_write(self):
        a, b = self.observe(), observe_binding(self.k, self.policies['entries']['t-b'], self.targets)
        self.assertTrue(a['guards']['ok']); self.assertTrue(b['guards']['ok'])
        self.assertEqual((1, 0), (a['quota'], b['quota']))
        self.assertNotEqual(a['clusterQueue']['metadata']['uid'], b['clusterQueue']['metadata']['uid'])
        self.assertFalse(any(method != 'GET' for method, _ in self.k.calls))

    def test_namespace_label_and_owner_uid_must_both_match(self):
        ns = self.k.objects['/api/v1/namespaces/example-tenant-queue-a']
        ns['metadata']['ownerReferences'][0]['uid'] = 't-b'
        self.assertFalse(self.observe()['guards']['ok'])

    def test_replaced_tenant_or_flavor_is_not_adopted(self):
        for path in [TENANTS + '/example-tenant-a', RF + '/example-gpu-flavor']:
            original = self.k.objects[path]['metadata']['uid']
            self.k.objects[path]['metadata']['uid'] = 'replaced'
            self.assertFalse(self.observe()['guards']['ok'])
            self.k.objects[path]['metadata']['uid'] = original

    def test_hold_workload_and_lock_guards(self):
        cq = self.k.objects[CQ + '/example-tenant-queue-a']
        cq['spec']['stopPolicy'] = 'None'
        self.assertFalse(self.observe()['guards']['ok'])
        cq['spec']['stopPolicy'] = 'Hold'
        path = '/apis/kueue.x-k8s.io/v1beta2/namespaces/example-tenant-queue-a/workloads/w'
        self.k.objects[path] = {'metadata': {'name': 'w'}}
        self.assertFalse(self.observe()['guards']['ok'])
        del self.k.objects[path]
        self.k.objects['/api/v1/namespaces/example-tenant-queue-a/resourcequotas/execution-lock']['spec']['hard']['pods'] = '1'
        self.assertFalse(self.observe()['guards']['ok'])

    def test_wrong_local_queue_connection_and_broad_selector_rejected(self):
        path = '/apis/kueue.x-k8s.io/v1beta2/namespaces/example-tenant-queue-a/localqueues/example-tenant-a'
        self.k.objects[path]['spec']['clusterQueue'] = 'example-tenant-queue-b'
        self.assertFalse(self.observe()['guards']['ok'])
        self.k.objects[path]['spec']['clusterQueue'] = 'example-tenant-queue-a'
        self.k.objects[CQ + '/example-tenant-queue-a']['spec']['namespaceSelector'] = {}
        self.assertFalse(self.observe()['guards']['ok'])

    def test_capsule_quantity_unresolved_defers(self):
        tenant = self.k.objects[TENANTS + '/example-tenant-a']
        self.assertEqual('NotConfigured', self.observe()['capsuleQuota']['state'])
        tenant['spec']['resourceQuotas'] = {'items': [{'hard': {'requests.nvidia.com/gpu': '1'}}]}
        result = self.observe()
        self.assertEqual('CapsuleQuotaUnresolved', result['guards']['reason'])
        self.assertEqual('Present', result['capsuleQuota']['state'])

    def test_missing_usage_and_unavailable_queue_are_unknown(self):
        self.assertIsNone(self.observe()['usage'])
        self.k.fail_paths.add(CQ + '/example-tenant-queue-a')
        r = self.observe()
        self.assertIsNone(r['quota']); self.assertFalse(r['guards']['ok'])

    def test_uncovered_namespace_never_becomes_executable(self):
        for state in ['Unknown', 'NotCovered', 'Covered']:
            self.assertFalse(execution_binding(self.p, {'state': state})['executionEnabled'])
